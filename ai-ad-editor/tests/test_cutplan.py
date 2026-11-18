import json

import pytest
import requests

from adeditor import cli, cutplan, envelope, faces, keys, paths, render, speed, transcribe, verify
from adeditor.paths import Job

# Three words with clear gaps: tones at 0.5 to 1.0, 1.6 to 2.0 and 2.6 to 3.1.
THREE = [("s", 0.5), ("w", 0.5), ("s", 0.6), ("w", 0.4), ("s", 0.6), ("w", 0.5), ("s", 0.5)]


def strikes(*spans, fillers=(), approved=True):
    return {"strikes": [{"from": a, "to": b, "why": "test"} for a, b in spans],
            "fillers": list(fillers), "approved": approved}


def joins(plan):
    """(source time the kept piece stops, source time the next one starts) at every join."""
    segs = plan["segments"]
    return [(a["src"][1], b["src"][0]) for a, b in zip(segs, segs[1:])]


def quiet(env, t):
    """Every 10 ms frame within 30 ms of t sits at or below the line."""
    _, line = envelope.floor_and_line(env)
    return all(env[i] <= line for i in range(int((t - 0.03) * 100), int((t + 0.03) * 100) + 1))


def test_cuts_only_inside_silence(take):
    _, _, env = take([("w", 0.5), ("s", 0.6), ("w", 0.4), ("s", 0.25), ("w", 0.5)])
    # Word times sit a little inside the sound, as Scribe places them; the second gap is under MIN_GAP.
    words = [{"word": "one", "start": 0.0, "end": 0.42}, {"word": "two", "start": 1.18, "end": 1.48},
             {"word": "three", "start": 1.77, "end": 2.2}]
    plan = cutplan.plan(words, env, strikes(), 30.0)
    # Sound ends at 0.5 and starts again at 1.1: 0.10 s of silence stays on each side.
    assert joins(plan) == [(0.6, 1.0)]
    # The 0.25 s gap from 1.5 to 1.75 sits whole inside one kept piece.
    assert any(s["src"][0] <= 1.5 and s["src"][1] >= 1.75 for s in plan["segments"])
    assert all(quiet(env, a) and quiet(env, b) for a, b in joins(plan))
    assert plan["tight_joins"] == [] and plan["struck"] == []
    assert plan["duration"] == pytest.approx(0.6 + 1.25, abs=1e-3)
    assert plan["threshold_db"] == pytest.approx(plan["floor_db"] + 12, abs=0.11)


def test_every_kept_word_is_mapped_once(take):
    _, words, env = take([("s", 0.4)] + [("w", 0.4), ("s", 0.5)] * 6)
    plan = cutplan.plan(words, env, strikes((1, 2), fillers=[4]), 30.0)
    assert len(plan["words"]) == len(words) - 3
    assert [w["src"] for w in plan["words"]] == [words[i]["start"] for i in (0, 3, 5)]
    assert [w["text"] for w in plan["words"]] == ["w0", "w3", "w5"]
    assert [(w["text"], w["src"]) for w in plan["struck"]] == [(words[i]["word"], words[i]["start"]) for i in (1, 2, 4)]
    # Each struck word records where it was cut on the output clock: a join.
    assert all(w["t"] in [s["out"][1] for s in plan["segments"][:-1]] for w in plan["struck"])
    starts = [w["start"] for w in plan["words"]]
    assert starts == sorted(starts) and all(0 <= w["start"] < w["end"] <= plan["duration"] for w in plan["words"])


def test_struck_retake_is_removed_with_its_silence(take):
    _, words, env = take(THREE)
    plan = cutplan.plan(words, env, strikes((1, 1)), 30.0)
    assert [w["text"] for w in plan["words"]] == ["w0", "w2"]
    assert plan["struck"] == [{"text": "w1", "src": words[1]["start"], "t": plan["segments"][0]["out"][1]}]
    # One join: 0.10 s after the first word's sound (ends 1.0) to 0.10 s before the last's (starts 2.6).
    assert joins(plan) == [(1.1, 2.5)]
    assert all(quiet(env, a) and quiet(env, b) for a, b in joins(plan))
    assert plan["tight_joins"] == []


def synthetic(*parts):
    """A 10 ms envelope from (dB, seconds) parts."""
    env = []
    for db, seconds in parts:
        env += [float(db)] * int(round(seconds * 100))
    return env


def test_run_together_retake_edge_goes_on_the_quietest_frame():
    # Word 0, then 0.05 s at -40 dB only (above the line), then word 1 (struck), a clear gap, word 2.
    env = synthetic((-75, 0.5), (-23, 0.5), (-40, 0.05), (-23, 0.45), (-75, 0.6), (-23, 0.5), (-75, 0.5))
    words = [{"word": "so", "start": 0.5, "end": 1.0}, {"word": "so", "start": 1.05, "end": 1.5},
             {"word": "listen", "start": 2.18, "end": 2.52}]
    plan = cutplan.plan(words, env, strikes((1, 1)), 30.0)
    (edge, after), = joins(plan)
    assert 1.0 <= edge <= 1.05 and env[int(edge * 100)] == -40
    assert after == 2.0 and quiet(env, after)
    assert plan["tight_joins"] == [plan["segments"][0]["out"][1]]
    assert [w["text"] for w in plan["words"]] == ["so", "listen"]
    # The words either side of every tight join, for the person to listen to.
    first, second = plan["words"]
    assert plan["tight_words"] == [{"t": plan["tight_joins"][0], "before": {"text": "so", "t": first["start"]},
                                    "after": {"text": "listen", "t": second["start"]}}]


def test_a_tight_edge_never_cuts_into_the_kept_word():
    # A struck word runs into the kept one over 30 ms at -40 dB; the kept word opens loud, then dips.
    env = synthetic((-75, 0.5), (-23, 0.54), (-40, 0.03), (-25, 0.02), (-32, 0.03), (-23, 0.4), (-75, 0.6),
                    (-23, 0.4), (-75, 0.5))
    words = [{"word": "the", "start": 0.5, "end": 1.0}, {"word": "the", "start": 1.1, "end": 1.45},
             {"word": "best", "start": 2.12, "end": 2.45}]
    plan = cutplan.plan(words, env, strikes((0, 0)), 30.0)
    start = plan["segments"][0]["src"][0]
    assert start <= 1.07  # the kept word's sound starts at 1.07: none of it is cut
    assert start >= 1.04 - 1 / 30  # at most one frame of the struck word stays in
    assert plan["tight_joins"][0] == 0.0


def test_a_short_struck_word_run_together_on_both_sides_still_comes_out():
    # A struck "the" of 0.10 s runs into the words either side, with quiet dips inside it.
    env = synthetic((-75, 0.5), (-23, 0.5), (-30, 0.02), (-23, 0.03), (-45, 0.01), (-23, 0.02), (-45, 0.01),
                    (-23, 0.03), (-30, 0.02), (-23, 0.4), (-75, 0.6), (-23, 0.4), (-75, 0.6))
    words = [{"word": "we", "start": 0.5, "end": 1.0}, {"word": "the", "start": 1.02, "end": 1.12},
             {"word": "the", "start": 1.14, "end": 1.54}, {"word": "best", "start": 2.14, "end": 2.54}]
    plan = cutplan.plan(words, env, strikes((1, 1)), 30.0)

    def kept(t):
        return any(s["src"][0] <= t < s["src"][1] for s in plan["segments"])

    assert all(kept(i / 100) for i in range(50, 100)) and all(kept(i / 100) for i in range(114, 154))
    assert not any(kept(i / 1000) for i in range(1040, 1100))  # the struck word's middle is out
    assert plan["tight_joins"] and plan["tight_joins"][0] == plan["segments"][0]["out"][1]
    assert [w["text"] for w in plan["words"]] == ["we", "the", "best"]


def test_a_struck_word_too_short_to_cut_on_whole_frames_stops_naming_it():
    env = synthetic((-75, 0.5), (-23, 0.51), (-30, 0.02), (-23, 0.4), (-75, 0.6), (-23, 0.4), (-75, 0.6))
    words = [{"word": "we", "start": 0.5, "end": 1.0}, {"word": "uh", "start": 1.01, "end": 1.03},
             {"word": "care", "start": 1.04, "end": 1.43}, {"word": "lots", "start": 2.03, "end": 2.43}]
    with pytest.raises(cutplan.CannotCut, match="'uh'"):
        cutplan.plan(words, env, strikes(fillers=[1]), 30.0)
    assert issubclass(cutplan.CannotCut, ValueError)  # the cut command reports it plainly


def test_a_kept_word_time_past_the_quiet_moment_stops():
    # The kept word's sound ends at 1.0, then 30 ms at -40 dB, then the struck word; the transcript
    # has the kept word end late (1.04) and the struck word start late (1.08). The cut never looks
    # back inside a kept word's own times, so it can't find the quiet moment and stops.
    env = synthetic((-75, 0.5), (-23, 0.5), (-40, 0.03), (-23, 0.27), (-75, 0.6), (-23, 0.4), (-75, 0.5))
    words = [{"word": "we", "start": 0.5, "end": 1.04}, {"word": "um", "start": 1.08, "end": 1.25},
             {"word": "care", "start": 1.98, "end": 2.32}]
    with pytest.raises(cutplan.CannotCut, match="'um'"):
        cutplan.plan(words, env, strikes(fillers=[1]), 30.0)


@pytest.mark.parametrize("fps", [30.0, 25.0, 60.0])
def test_a_kept_words_own_closure_is_never_cut_on(fps):
    # Kept "what" with its own stop closure at 0.95 (one -35 dB frame), its release loud to 1.0,
    # then struck "the" with no dip between them. Exact transcript times. Cutting on the closure
    # would lose the release; the only right answer is to stop.
    env = synthetic((-75, 0.5), (-23, 0.45), (-35, 0.01), (-23, 0.04), (-23, 0.1), (-75, 0.4), (-23, 0.4),
                    (-75, 0.6))
    words = [{"word": "what", "start": 0.5, "end": 1.0}, {"word": "the", "start": 1.0, "end": 1.1},
             {"word": "best", "start": 1.5, "end": 1.9}]
    with pytest.raises(cutplan.CannotCut, match="'the'"):
        cutplan.plan(words, env, strikes((1, 1)), fps)


def test_no_quiet_moment_beside_a_kept_word_stops_instead_of_clipping_it():
    # The struck word runs straight into the kept one with no dip, and both transcript times are
    # late, so every frame near the boundary is a word's own sound.
    env = synthetic((-75, 0.5), (-23, 0.4), (-40, 0.03), (-22, 0.09), (-25, 0.03), (-23, 0.37), (-75, 0.6),
                    (-23, 0.4), (-75, 0.5))
    words = [{"word": "we", "start": 0.5, "end": 0.91}, {"word": "the", "start": 0.96, "end": 1.04},
             {"word": "best", "start": 1.05, "end": 1.39}, {"word": "care", "start": 1.99, "end": 2.39}]
    with pytest.raises(cutplan.CannotCut, match="struck word 'the' at 0:00"):
        cutplan.plan(words, env, strikes((1, 1)), 30.0)


def test_a_tight_cut_never_lands_inside_a_kept_words_own_times():
    # Kept "best" starts exactly at 0.88 and has a dip of its own at 0.89; struck "the" runs
    # straight into it with no dip, and its transcript end (0.909) overlaps "best". The dip inside
    # "best" must never be cut on, whatever the struck word's times say.
    def take_with(boundary_db):
        return synthetic((-75, 0.5), (-23, 0.21), (-40, 0.02), (-23, 0.14), (boundary_db, 0.01), (-23, 0.01),
                         (-39, 0.01), (-23, 0.22), (-75, 0.6), (-23, 0.3), (-75, 0.6))

    words = [{"word": "we", "start": 0.5, "end": 0.71}, {"word": "the", "start": 0.721, "end": 0.909},
             {"word": "best", "start": 0.88, "end": 1.12}, {"word": "care", "start": 1.72, "end": 2.02}]
    with pytest.raises(cutplan.CannotCut, match="'the'"):
        cutplan.plan(words, take_with(-23), strikes((1, 1)), 60.0)
    # With a real dip just before "best" (0.87 to 0.88), the cut goes there and keeps all of "best".
    plan = cutplan.plan(words, take_with(-40), strikes((1, 1)), 60.0)
    assert plan["segments"][1]["src"][0] <= 0.87


def test_beside_names_the_words_by_the_piece_they_play_in():
    # The plan beside.py produced before the clamp: "best" maps to the second piece at 0.383, just
    # under the join's 0.3833 by rounding, and must still be the word after it.
    segments = [{"src": [0.35, 0.7333], "out": [0.0, 0.3833]}, {"src": [0.8667, 1.2667], "out": [0.3833, 0.7833]},
                {"src": [1.5833, 2.3667], "out": [0.7833, 1.5667]}]
    words = [{"text": "we", "start": 0.15}, {"text": "best", "start": 0.383}, {"text": "care", "start": 0.92}]
    assert cutplan._beside(words, [0, 1, 2], segments, 1) == {
        "t": 0.3833, "before": {"text": "we", "t": 0.15}, "after": {"text": "best", "t": 0.383}}
    assert cutplan._beside(words, [0, 1, 2], segments, 0)["before"] is None  # a tight lead-in
    assert cutplan._beside(words, [0, 1, 2], segments, 3) == {
        "t": 1.5667, "before": {"text": "care", "t": 0.92}, "after": None}  # a tight tail


def test_beside_py_case_stops():
    # "best" has an early transcript start (0.85; its sound starts 0.89) and "the" a late end
    # (0.909): the only dip sits inside "best"'s own times, so the cut stops.
    env = synthetic((-75, 0.5), (-23, 0.21), (-45, 0.01), (-23, 0.16), (-45, 0.01), (-23, 0.23), (-75, 0.6),
                    (-23, 0.3), (-75, 0.6))
    words = [{"word": "we", "start": 0.5, "end": 0.71}, {"word": "the", "start": 0.72, "end": 0.909},
             {"word": "best", "start": 0.85, "end": 1.12}, {"word": "care", "start": 1.72, "end": 2.02}]
    with pytest.raises(cutplan.CannotCut, match="'the'"):
        cutplan.plan(words, env, strikes((1, 1)), 60.0)


def test_a_struck_span_at_either_end_moves_the_lead_in_and_tail(take):
    # Tones at 0.5, 1.4, 2.3 and 3.2, each 0.4 s; the first is struck and the last is a filler.
    _, words, env = take([("s", 0.5)] + [("w", 0.4), ("s", 0.5)] * 4)
    plan = cutplan.plan(words, env, strikes((0, 0), fillers=[3]), 30.0)
    assert plan["segments"][0]["src"][0] == 1.3  # after the struck sound, 0.10 s before the kept word
    assert plan["segments"][-1]["src"][1] == 2.8  # 0.10 s after the last kept sound, before the filler
    assert [w["text"] for w in plan["words"]] == ["w1", "w2"]
    assert [w["text"] for w in plan["struck"]] == ["w0", "w3"]


def test_wide_keep_widens_one_join_only(take):
    _, words, env = take(THREE)
    plan = cutplan.plan(words, env, strikes(), 30.0)
    assert joins(plan) == [(1.1, 1.5), (2.1, 2.5)]
    wider = cutplan.plan(words, env, strikes(), 30.0, wide=[1.3])
    # 0.16 s of silence kept beside the sound at the first join, snapped to frames toward the sound.
    assert joins(wider) == [(1.1333, 1.4667), (2.1, 2.5)]


def test_only_the_join_nearest_a_near_word_is_widened():
    cut = {"segments": [{"src": [0.5, 1.0], "out": [0.0, 0.5]}, {"src": [1.4, 2.0], "out": [0.5, 1.1]},
                        {"src": [2.6, 3.0], "out": [1.1, 1.5]}], "duration": 1.5, "tight_joins": [0.0]}
    near = [{"kind": "missing", "kept": "x", "heard": "", "t": 1.2, "to_join": 0.1}]
    assert cutplan._wide_points(cut, near) == [2.3]  # inside the stretch cut at the join at 1.1
    # Nearest the tight lead-in: there's no join there to widen.
    assert cutplan._wide_points(cut, [{**near[0], "t": 0.1}]) == []
    # Near only because it's a struck word heard again, with no join within a second: nothing to widen.
    assert cutplan._wide_points(cut, [{**near[0], "kind": "extra", "to_join": 3.0}]) == []
    assert cutplan._wide_points(cut, [{**near[0], "kind": "extra", "to_join": None}]) == []


def test_cut_on_sound_is_refused():
    env = synthetic((-75, 0.5), (-23, 1.0), (-75, 0.5))
    plan = {"segments": [{"src": [0.0, 1.0], "out": [0.0, 1.0]}, {"src": [1.6, 2.0], "out": [1.0, 1.4]}],
            "tight_joins": []}
    with pytest.raises(cutplan.CutOnSound) as err:
        cutplan.check(plan, env)
    assert err.value.times == [1.0]
    assert "0:01" in str(err.value)
    cutplan.check({**plan, "tight_joins": [1.0]}, env)  # a tight join is the word check's to judge


def test_plan_refuses_strikes_past_the_words(take):
    _, words, env = take(THREE)
    with pytest.raises(ValueError):
        cutplan.plan(words, env, strikes((2, 5)), 30.0)
    with pytest.raises(ValueError, match="nothing left to keep"):
        cutplan.plan(words, env, strikes((0, 2)), 30.0)
    with pytest.raises(ValueError, match="no words"):
        cutplan.plan([], env, strikes(), 30.0)


# The cut command, end to end: real ffmpeg, the transcriber mocked.


@pytest.fixture
def tracked(monkeypatch):
    """Face tracking stubbed (it has its own tests): records the clip it was given."""
    seen = []

    def fake(video, fps=2, cut=None):
        seen.append(video)
        samples = [{"t": 0.0, "x": 0.3, "y": 0.2, "w": 0.2, "h": 0.25}]
        (video.parent / "faces.json").write_text(json.dumps({"cut": cut, "samples": samples}))
        return samples

    monkeypatch.setattr(faces, "track", fake)
    return seen


@pytest.fixture
def job(tmp_path, monkeypatch, take, video, tracked):
    monkeypatch.setattr(paths, "ROOT", tmp_path / "root")
    wav, words, env = take(THREE, texts=["Northbank", "fixes", "knees."])
    job = Job("acme", video(4, 320, 240, wav))
    job.work.mkdir(parents=True)
    (job.work / "transcript.json").write_text(json.dumps({"words": words}))
    (job.work / "envelope.json").write_text(json.dumps(env))
    (job.work / "strikes.json").write_text(json.dumps(strikes()))
    job.brand.mkdir(parents=True)
    (job.brand / "kit.json").write_text(json.dumps({"keyterms": ["Northbank"]}))
    return job


def hear(monkeypatch, job, drop_on=()):
    """Mock Scribe: it hears the words cut.json kept, except 'Northbank' on the listed calls."""
    calls = []

    def fake(audio, keyterms, cache_dir):
        calls.append(keyterms)
        kept = json.loads((job.work / "cut.json").read_text())["words"]
        heard = [{"word": w["text"], "start": w["start"], "end": w["end"]} for w in kept]
        if len(calls) in drop_on:
            heard = [w for w in heard if w["word"] != "Northbank"]
        return heard

    monkeypatch.setattr(transcribe, "transcribe", fake)
    return calls


def run_cut(job):
    return cli.main(["cut", job.client, str(job.video)])


def test_cut_refuses_until_the_strikes_are_approved(job, monkeypatch, capsys):
    (job.work / "strikes.json").write_text(json.dumps(strikes(approved=False)))
    calls = hear(monkeypatch, job)
    assert run_cut(job) == 1
    assert "approve-cut" in capsys.readouterr().err
    assert not (job.work / "cut.json").exists() and calls == []


def test_cut_writes_the_plan_the_aroll_and_the_word_check(job, monkeypatch, capsys):
    calls = hear(monkeypatch, job)
    assert run_cut(job) == 0
    assert calls == [["Northbank"]]
    cut = json.loads((job.work / "cut.json").read_text())
    assert joins(cut) == [(1.1, 1.5), (2.1, 2.5)]
    assert (job.work / "aroll.mp4").exists()
    heard = json.loads((job.work / "transcript.cut.json").read_text())["words"]
    assert [w["word"] for w in heard] == ["Northbank", "fixes", "knees."]
    report = json.loads((job.work / "verify.json").read_text())
    assert report == {"cut": verify.fingerprint(cut), "matched": 3, "total": 3, "near": [], "far": [], "accepted": []}
    assert "3 of 3" in capsys.readouterr().out


def test_a_near_diff_widens_that_join_and_checks_again(job, monkeypatch, capsys):
    calls = hear(monkeypatch, job, drop_on=(1,))
    assert run_cut(job) == 0
    assert len(calls) == 2
    cut = json.loads((job.work / "cut.json").read_text())
    assert joins(cut) == [(1.1333, 1.4667), (2.1, 2.5)]  # only the join next to 'Northbank' moved
    assert json.loads((job.work / "verify.json").read_text())["near"] == []
    assert "widened" in capsys.readouterr().out


def test_still_near_after_widening_stops_naming_the_line(job, monkeypatch, capsys):
    calls = hear(monkeypatch, job, drop_on=(1, 2))
    assert run_cut(job) == 1
    assert len(calls) == 2
    err = capsys.readouterr().err
    assert "The word 'Northbank' near 0:00 may be clipped" in err
    assert "Northbank fixes knees." in err
    # nothing was struck, so the way forward is to listen, and say so if every word is there
    assert "strike" not in err
    assert f'Listen to aroll.mp4 in {job.work} at 0:00; if every word is there, run adeditor accept-words acme "{job.video}".' in err
    for name in ("cut.json", "aroll.mp4", "transcript.cut.json", "verify.json"):
        assert (job.work / name).exists()
    assert json.loads((job.work / "verify.json").read_text())["near"][0]["kind"] == "missing"


@pytest.mark.parametrize("error,said", [
    (transcribe.ScribeError("ElevenLabs Scribe returned HTTP 500: busy"), "ElevenLabs Scribe returned HTTP 500: busy"),
    (requests.ConnectionError("no route to host"), "check your internet connection"),
    (keys.MissingKey(keys.ELEVENLABS), str(keys.MissingKey(keys.ELEVENLABS))),
])
def test_a_transcriber_failure_stops_plainly(job, monkeypatch, capsys, error, said):
    def fail(audio, keyterms, cache_dir):
        raise error

    monkeypatch.setattr(transcribe, "transcribe", fail)
    assert run_cut(job) == 1
    err = capsys.readouterr().err
    assert said in err and "Traceback" not in err
    assert (job.work / "cut.json").exists() and (job.work / "aroll.mp4").exists()


def test_a_bug_is_not_reported_as_a_transcriber_failure(job, monkeypatch, capsys):
    def broken(audio, keyterms, cache_dir):
        raise RecursionError("a bug, not the transcriber")

    monkeypatch.setattr(transcribe, "transcribe", broken)
    monkeypatch.delenv("ADEDITOR_DEBUG", raising=False)
    assert run_cut(job) == 1
    err = capsys.readouterr().err
    assert "RecursionError" in err and "couldn't run" not in err and "Traceback" not in err
    monkeypatch.setenv("ADEDITOR_DEBUG", "1")
    assert run_cut(job) == 1
    assert "Traceback" in capsys.readouterr().err


def test_cut_says_where_strikes_json_is_broken(job, monkeypatch, capsys):
    calls = hear(monkeypatch, job)
    (job.work / "strikes.json").write_text('{"strikes": [], "fillers": [],\n "approved": true,}')
    assert run_cut(job) == 1
    err = capsys.readouterr().err
    assert "strikes.json isn't valid JSON" in err and "line 2" in err and "Traceback" not in err
    assert calls == []


def test_cut_says_a_broken_kit_is_broken(job, monkeypatch, capsys):
    calls = hear(monkeypatch, job)
    (job.brand / "kit.json").write_text("{'keyterms': ['Northbank']}")
    assert run_cut(job) == 1
    err = capsys.readouterr().err
    assert "kit.json isn't valid JSON" in err and "client new" not in err and "Traceback" not in err
    assert calls == []


def test_the_cut_names_the_words_at_each_tight_join(job, monkeypatch, capsys):
    hear(monkeypatch, job)
    tight = {"t": 0.7667, "before": {"text": "Northbank", "t": 0.2467}, "after": {"text": "fixes", "t": 0.9467}}
    real_plan = cutplan.plan

    def with_a_tight_join(*args, **kw):
        cut = real_plan(*args, **kw)
        return {**cut, "tight_joins": [0.7667], "tight_words": [tight]}

    monkeypatch.setattr(cutplan, "plan", with_a_tight_join)
    assert run_cut(job) == 0
    assert "between 'Northbank' and 'fixes' near 0:00" in capsys.readouterr().out


def test_cut_tracks_the_face_on_the_new_aroll(job, monkeypatch, tracked):
    hear(monkeypatch, job)
    assert run_cut(job) == 0
    assert tracked == [job.work / "aroll.mp4"]


def test_cut_tracks_the_1x_clip_when_a_speed_is_set(job, monkeypatch, tracked):
    hear(monkeypatch, job)
    (job.work / "speed.json").write_text(json.dumps({"speed": 1.25}))
    assert run_cut(job) == 0
    assert speed.current(job) == 1.25
    assert tracked == [job.work / "aroll.1x.mp4"]


def test_cut_warns_when_the_face_is_lost_but_keeps_the_cut(job, monkeypatch, capsys):
    hear(monkeypatch, job)

    def lost(video, fps=2, cut=None):
        raise faces.NoFace(0.2)

    monkeypatch.setattr(faces, "track", lost)
    assert run_cut(job) == 0
    assert "20%" in capsys.readouterr().err
    assert (job.work / "aroll.mp4").exists()


def test_cut_without_the_face_model_says_how_to_get_it(job, monkeypatch, capsys):
    hear(monkeypatch, job)

    def missing(video, fps=2, cut=None):
        raise faces.ModelMissing("The face model isn't downloaded. Run: adeditor doctor --fix")

    monkeypatch.setattr(faces, "track", missing)
    assert run_cut(job) == 1
    assert "adeditor doctor --fix" in capsys.readouterr().err


# A word check stop: the face map still belongs to the new cut, the stop names a strike only when one
# is near, and the person's yes after listening is recorded for that exact cut.


def heard_as(monkeypatch, job, swap):
    """Mock Scribe: it hears the kept words with some heard as others, on every call."""
    calls = []

    def fake(audio, keyterms, cache_dir):
        calls.append(1)
        kept = json.loads((job.work / "cut.json").read_text())["words"]
        return [{"word": swap.get(w["text"], w["text"]), "start": w["start"], "end": w["end"]} for w in kept]

    monkeypatch.setattr(transcribe, "transcribe", fake)
    return calls


def current_cut(job):
    return json.loads((job.work / "cut.json").read_text())


def test_a_word_check_stop_still_maps_the_face_on_the_new_cut(job, monkeypatch, tracked, capsys):
    heard_as(monkeypatch, job, {"fixes": "fix"})
    assert run_cut(job) == 1
    assert tracked == [job.work / "aroll.mp4"]
    fp = verify.fingerprint(current_cut(job))
    assert faces.load(job.work / "faces.json")[0] == fp
    saved = json.loads((job.work / "verify.json").read_text())
    assert saved["cut"] == fp and saved["accepted"] == []
    assert [(d["kind"], d["kept"], d["heard"]) for d in saved["near"]] == [("changed", "fixes", "fix")]


def test_a_stop_next_to_a_cut_word_names_that_strike(tmp_path, monkeypatch, take, video, tracked, capsys):
    monkeypatch.setattr(paths, "ROOT", tmp_path / "root")
    wav, words, env = take(THREE, texts=["Northbank", "um", "knees."])
    job = Job("acme", video(4, 320, 240, wav))
    job.work.mkdir(parents=True)
    (job.work / "transcript.json").write_text(json.dumps({"words": words}))
    (job.work / "envelope.json").write_text(json.dumps(env))
    (job.work / "strikes.json").write_text(json.dumps(strikes(fillers=[1])))
    heard_as(monkeypatch, job, {"knees.": "keys."})
    assert run_cut(job) == 1
    err = capsys.readouterr().err
    assert "'um'" in err and "strikes.json" in err and "adeditor accept-words acme" in err


def test_accept_words_records_the_yes_for_this_cut_and_the_render_goes_on(job, monkeypatch, capsys):
    heard_as(monkeypatch, job, {"fixes": "fix"})
    assert run_cut(job) == 1
    with pytest.raises(render.Refused) as refused:
        render.check_words(job)
    assert "adeditor accept-words acme" in str(refused.value)
    capsys.readouterr()
    assert cli.main(["accept-words", job.client, str(job.video)]) == 0
    assert "0:01" in capsys.readouterr().out
    saved = json.loads((job.work / "verify.json").read_text())
    assert saved["accepted"] == saved["near"] and saved["near"]
    render.check_words(job)  # no refusal now
    assert cli.main(["accept-words", job.client, str(job.video)]) == 0
    assert "nothing" in capsys.readouterr().out.lower()


def test_the_same_cut_again_keeps_the_yes(job, monkeypatch, capsys):
    calls = heard_as(monkeypatch, job, {"fixes": "fix"})
    assert run_cut(job) == 1
    assert cli.main(["accept-words", job.client, str(job.video)]) == 0
    capsys.readouterr()
    assert run_cut(job) == 0  # the same cut and the same difference: the person already listened
    assert "every word is there" in capsys.readouterr().out
    assert len(calls) >= 2


def test_a_new_difference_is_not_covered_by_an_old_yes(job, monkeypatch, capsys):
    heard_as(monkeypatch, job, {"fixes": "fix"})
    assert run_cut(job) == 1
    assert cli.main(["accept-words", job.client, str(job.video)]) == 0
    heard_as(monkeypatch, job, {"fixes": "fax"})
    assert run_cut(job) == 1
    assert "'fax'" in capsys.readouterr().err


def test_accept_words_refuses_a_check_made_on_another_cut(job, monkeypatch, capsys):
    heard_as(monkeypatch, job, {"fixes": "fix"})
    assert run_cut(job) == 1
    cut = current_cut(job)
    cut["segments"][0]["src"][1] += 0.1  # the cut changed since the check
    (job.work / "cut.json").write_text(json.dumps(cut))
    capsys.readouterr()
    assert cli.main(["accept-words", job.client, str(job.video)]) == 1
    assert "adeditor cut acme" in capsys.readouterr().err
    assert json.loads((job.work / "verify.json").read_text())["accepted"] == []


def test_a_transcriber_failure_still_leaves_the_files_in_step(job, monkeypatch, tracked, capsys):
    hear(monkeypatch, job)
    (job.work / "speed.json").write_text(json.dumps({"speed": 1.25}))

    def fail(audio, keyterms, cache_dir):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(transcribe, "transcribe", fail)
    assert run_cut(job) == 1
    one_x = json.loads((job.work / "cut.1x.json").read_text())
    assert current_cut(job)["duration"] == pytest.approx(one_x["duration"] / 1.25, abs=1e-3)
    assert faces.load(job.work / "faces.json")[0] == verify.fingerprint(one_x)


def test_a_cut_that_stops_part_way_leaves_nothing_old_to_pass_for_it(job, monkeypatch, capsys):
    hear(monkeypatch, job)
    assert run_cut(job) == 0
    assert (job.work / "verify.json").exists() and (job.work / "faces.json").exists()

    def broken(*a, **k):
        raise cutplan.media.ToolError("ffmpeg failed (exit 1):\nno space left")

    monkeypatch.setattr(cutplan.aroll, "build", broken)
    assert run_cut(job) == 1
    assert not (job.work / "verify.json").exists() and not (job.work / "faces.json").exists()
