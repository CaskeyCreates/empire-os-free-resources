import json
import shutil
from pathlib import Path

import pytest
from conftest import put_in_raw

from adeditor import cli, paths, plan
from adeditor.paths import Job

TEXT = ("If you sit at a desk all day your back is already telling you something. "
        "You don't have to wait for things to get worse before things change. "
        "Most people come to us stuck in the same stiff routine and we start with the appointments "
        "then the therapists build your plan. "
        "In six weeks you can actually do it again. "
        "Book a call and we will get you moving freely.")
STEP = 0.35


def cut_words(text=TEXT):
    words, t = [], 0.0
    for raw in text.split():
        words.append({"text": raw, "start": round(t, 3), "end": round(t + 0.3, 3), "src": round(t, 3)})
        t += STEP
    return words


@pytest.fixture
def setup(tmp_path, monkeypatch, video):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    job = Job("acme", Path("take1.mp4"))
    put_in_raw()
    job.work.mkdir(parents=True)
    words = cut_words()
    (job.work / "cut.json").write_text(json.dumps({"words": words, "duration": words[-1]["end"] + 0.3}), encoding="utf-8")
    job.broll_cache.mkdir(parents=True)
    for clip_id in ("pexels-1234567", "pexels-7654321", "pixabay-1234567"):
        shutil.copy(video(10, 360, 640) if clip_id != "pexels-7654321" else video(10, 320, 568), job.broll_cache / f"{clip_id}.mp4")
    kit = job.brand / "kit.json"
    kit.parent.mkdir(parents=True)
    kit.write_text(json.dumps({"approved": False, "lockup": {
        "callout": "For desk workers with back pain", "offer": ["Six weeks to moving freely"],
        "detail": None, "button": "Learn more"}}), encoding="utf-8")

    def write(**plan_json):
        body = {"lockup": "kit", **plan_json}
        (job.work / "plan.json").write_text(json.dumps(body), encoding="utf-8")
        return job

    write.words = words
    return write


def word(words, text, nth=0):
    return [w for w in words if w["text"].strip(".,").lower() == text][nth]


def errors_of(job):
    with pytest.raises(plan.PlanError) as err:
        plan.load(job)
    return err.value.errors


def test_phrases_resolve_on_the_cut_clock(setup):
    job = setup(
        headings=[{"lines": ["You don't have to wait", "*for things to get worse.*"],
                   "from": "you don't have to wait", "to": "before things change", "hold": 0.7}],
        key_words=["stuck", {"word": "do", "only_in": "actually do it"}],
        broll=[{"clip": "pexels-1234567", "in": 1.5, "from": "the appointments", "to": "the therapists", "y": 40, "why": "the appointments"}],
        end_card=3.2, bed_offset_db=9)
    r = plan.load(job)
    w = setup.words
    you = [x for x in w if x["text"] == "You"][0]
    before = word(w, "before")
    assert r["headings"] == [{"lines": ["You don't have to wait", "*for things to get worse.*"],
                              "start": you["start"], "end": before["start"]}]
    appt = word(w, "the", 1)  # "the appointments": the second "the"
    assert w[w.index(appt) + 1]["text"] == "appointments"
    ther = [x for x in w if x["text"] == "therapists"][0]
    [b] = r["broll"]
    assert b["start"] == round(appt["start"] - 0.04, 3)
    assert b["end"] == round(w[w.index(ther) - 1]["start"] - 0.04, 3)
    assert b["clip"] == "pexels-1234567" and b["in"] == 1.5 and b["y"] == 40 and b["why"] == "the appointments"
    assert b["file"] == job.broll_cache / "pexels-1234567.mp4"
    assert r["lockup"] == {"callout": "For desk workers with back pain", "offer": ["Six weeks to moving freely"],
                           "detail": None, "button": "Learn more"}
    assert r["key_words"] == [{"word": "stuck", "only_in": None}, {"word": "do", "only_in": "actually do it"}]
    assert r["end_card"] == 3.2
    assert r["bed_offset_db"] == 6  # clamped


def test_to_end_holds_after_the_phrase(setup):
    job = setup(headings=[{"lines": ["You don't have to wait"], "from": "you don't",
                           "to": "have to wait", "to_end": True, "hold": 0.5}])
    r = plan.load(job)
    wait = [x for x in setup.words if x["text"] == "wait"][0]
    assert r["headings"][0]["end"] == round(wait["end"] + 0.5, 3)


def test_a_heading_can_show_while_one_phrase_is_said(setup):
    # from and to the same phrase, with to_end: it shows from the phrase's start to its end plus hold
    job = setup(headings=[{"lines": ["You don't have to wait"], "from": "you don't have to wait",
                           "to": "you don't have to wait", "to_end": True, "hold": 0.5}])
    [h] = plan.load(job)["headings"]
    assert h["start"] == word(setup.words, "you", 2)["start"]
    assert h["end"] == round(word(setup.words, "wait")["end"] + 0.5, 3)
    # B-roll reads to the same way
    job = setup(broll=[{"clip": "pexels-1234567", "from": "stuck in the same", "to": "stuck in the same", "to_end": True,
                        "hold": 0.4, "why": "x"}])
    [b] = plan.load(job)["broll"]
    assert b["start"] == round(word(setup.words, "stuck")["start"] - 0.04, 3)
    assert b["end"] == round(word(setup.words, "same")["end"] + 0.4 - 0.04, 3)


def test_with_to_end_to_may_sit_inside_the_from_match_and_without_it_comes_after(setup):
    # "things" is said twice: "for things to get worse before things change"
    first, second = word(setup.words, "things"), word(setup.words, "things", 1)
    job = setup(headings=[{"lines": ["You don't have to wait"], "from": "for things", "to": "things", "to_end": True}])
    assert plan.load(job)["headings"][0]["end"] == round(first["end"] + 0.7, 3)
    job = setup(headings=[{"lines": ["You don't have to wait"], "from": "for things", "to": "things"}])
    assert plan.load(job)["headings"][0]["end"] == second["start"]
    # without to_end, the same phrase as from isn't found after it
    job = setup(headings=[{"lines": ["You don't have to wait"], "from": "you don't have to wait",
                           "to": "you don't have to wait"}])
    assert any('can\'t find "you don\'t have to wait"' in e for e in errors_of(job))


def test_after_narrows_the_search(setup):
    # "you" is said five times; unconstrained the first is at 0.7 s, but after "stuck in" it is the one in "weeks you can"
    first_you = [x for x in setup.words if x["text"] == "you"][0]
    later = [x for x in setup.words if x["text"] == "you" and x["start"] > 15][0]
    assert first_you["start"] != later["start"]
    job = setup(broll=[{"clip": "pexels-1234567", "from": "you", "after": "stuck in", "to": "do it", "why": "x"}])
    assert plan.load(job)["broll"][0]["start"] == round(later["start"] - 0.04, 3)
    # without "after" the first "you" wins, which is inside the lockup and refused
    job = setup(broll=[{"clip": "pexels-1234567", "from": "you", "to": "sit at", "why": "x"}])
    assert any("lockup" in e for e in errors_of(job))


def test_a_heading_may_not_run_into_the_end_card(setup):
    job = setup(headings=[{"lines": ["Book a call"], "from": "book a call", "to": "and we", "to_end": True, "hold": 2.0}])
    assert any("Heading 1" in e and "end card" in e for e in errors_of(job))


def test_a_missing_phrase_names_close_ones(setup):
    job = setup(broll=[{"clip": "pexels-1234567", "from": "the appointment", "to": "the therapists", "why": "x"}])
    errs = errors_of(job)
    assert len(errs) == 1 and 'the appointment' in errs[0]
    assert "the appointments" in errs[0]
    assert errs[0].count(",") <= 3  # at most three suggestions


def test_heading_must_be_the_speakers_words(setup):
    job = setup(headings=[{"lines": ["You never have to wait"], "from": "you don't have to wait", "to": "before things change"}])
    errs = errors_of(job)
    assert any("headings use the speaker's own words" in e for e in errs)


def test_overlaps_are_refused(setup):
    w = setup.words
    job = setup(
        headings=[{"lines": ["You don't have to wait"], "from": "you don't have to wait", "to": "for things to get worse"}],
        broll=[
            {"clip": "pexels-1234567", "from": "the appointments", "to": "the therapists", "why": "a"},
            {"clip": "pexels-7654321", "from": "appointments then", "to": "build your plan", "why": "b"},
            {"clip": "pexels-1234567", "from": "if you sit", "to": "all day", "why": "c"},
            {"clip": "pexels-7654321", "from": "you don't have to wait", "to": "get worse", "why": "d"},
        ])
    errs = " ".join(errors_of(job))
    assert "overlap" in errs
    assert "lockup" in errs  # "if you sit" starts at 0
    assert "heading" in errs  # heading 1 runs through B-roll d


def test_a_broll_starting_at_one_second_fails_the_lockup_rule(setup):
    job = setup(broll=[{"clip": "pexels-1234567", "from": "sit at a desk", "to": "all day", "why": "x"}])
    errs = errors_of(job)
    assert any("lockup" in e and "2.5" in e for e in errs)


def test_broll_may_not_run_into_the_end_card(setup):
    job = setup(broll=[{"clip": "pexels-1234567", "from": "book a call", "to": "moving freely", "to_end": True, "hold": 1.0, "why": "x"}])
    assert any("end card" in e for e in errors_of(job))


def test_more_than_two_headings_and_a_missing_clip_file(setup):
    h = {"lines": ["You don't have to wait"], "from": "you don't have to wait", "to": "for things"}
    job = setup(headings=[h, h, h],
                broll=[{"clip": "pexels-9999999", "from": "the appointments", "to": "the therapists", "why": "x"}])
    errs = errors_of(job)
    assert any("at most 2 headings" in e for e in errs)
    assert any("pexels-9999999" in e and "broll" in e for e in errs)


def test_every_error_is_listed_and_cli_exits_1(setup, capsys):
    setup(headings=[{"lines": ["Nope"], "from": "zzz", "to": "qqq"}],
          broll=[{"clip": "pexels-1234567", "from": "xxxx", "to": "the therapists", "why": "x"}])
    assert cli.main(["plan", "check", "acme", "take1.mp4"]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") >= 3 and "zzz" in err and "xxxx" in err


def test_inline_lockup_is_validated(setup):
    job = setup(lockup={"callout": " ", "offer": []})
    assert any("lockup" in e for e in errors_of(job))
    job = setup(lockup={"callout": "For you", "offer": ["Six weeks"], "detail": "In clinic", "button": "Learn more"})
    assert plan.load(job)["lockup"]["detail"] == "In clinic"


def test_kit_lockup_must_not_be_empty(setup):
    job = setup()
    kit = json.loads((job.brand / "kit.json").read_text(encoding="utf-8"))
    kit["lockup"] = {"callout": "", "offer": [""], "detail": None, "button": "Learn more"}
    (job.brand / "kit.json").write_text(json.dumps(kit), encoding="utf-8")
    assert any("lockup" in e for e in errors_of(job))


def test_a_broll_clip_must_be_long_enough(setup):
    job = setup(broll=[{"clip": "pexels-1234567", "in": 9.5, "from": "the appointments", "to": "the therapists", "why": "x"}])
    assert any("long enough" in e or "only" in e for e in errors_of(job))


def test_the_example_plan_resolves(setup, capsys):
    example = Path(__file__).parent.parent / "docs" / "plan-example.json"
    # the example's wording is the TEXT above
    job = Job("acme", Path("take1.mp4"))
    (job.work / "plan.json").write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    r = plan.load(job)
    assert r["headings"] and r["broll"] and r["lockup"]["callout"]
    assert cli.main(["plan", "check", "acme", "take1.mp4"]) == 0
    out = capsys.readouterr().out
    assert "heading" in out and "b-roll" in out and "end card" in out


def test_the_end_card_starts_where_the_page_starts_it():
    # 0.2 s before the cut take ends, as the page and the mix place it, not before the last word's time
    cut = {"words": [{"text": "now.", "start": 9.0, "end": 9.4}], "duration": 9.95}
    assert plan.card_start(cut) == 9.75
