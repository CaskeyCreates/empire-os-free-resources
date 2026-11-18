import json
import shutil
from pathlib import Path

import pytest
from conftest import put_in_raw

from adeditor import graphic_plan, paths, plan
from adeditor.paths import Job

STEP = 0.35
FILL = "okay so here is some talk that carries on for a while "
CYCLE = "she gets anxious, she avoids, you step in, a little relief. "


def make_words(text):
    words, t = [], 0.0
    for raw in text.split():
        words.append({"text": raw, "start": round(t, 3), "end": round(t + 0.3, 3), "src": round(t, 3)})
        t += STEP
    return words


def kept_for(text):
    return plan.Kept(make_words(text))


def at(words, phrase, nth=0):
    """Start of the first word of the nth occurrence of a phrase, from the plain words."""
    toks = [w["text"].strip(".,'").lower() for w in words]
    want = phrase.split()
    hits = [i for i in range(len(toks) - len(want) + 1) if toks[i:i + len(want)] == want]
    return words[hits[nth]]["start"]


def run(graphics, text):
    kept = kept_for(text)
    errors: list[str] = []
    out = graphic_plan.resolve(graphics, kept, {"duration": kept.words[-1]["end"] + 0.3}, errors)
    return out, errors, kept.words


CYCLE_TEXT = FILL * 2 + CYCLE + FILL
CYCLE_SPEC = {"kind": "cycle", "from": "she gets anxious", "to": "a little relief", "to_end": True, "hold": 0.5,
              "items": ["she gets anxious", "she avoids", "you step in", "a little relief"]}


def test_cycle_items_land_on_their_words():
    out, errors, words = run([CYCLE_SPEC], CYCLE_TEXT)
    assert errors == []
    [g] = out
    assert g["kind"] == "cycle" and g["ground"] == "deep" and g["sfx"] is True and g["title"] is None
    # the first item sits on the window's first word, so it is held back to the end of the 0.4 s entrance
    assert [i["at"] for i in g["items"]] == [round(g["start"] + 0.4, 3), at(words, "she avoids"),
                                             at(words, "you step in"), at(words, "a little relief")]
    assert g["start"] == at(words, "she gets anxious")
    assert g["end"] == round(at(words, "relief") + 0.3 + 0.5, 3)  # to_end: after the phrase's last word


def test_items_resolve_in_order_when_a_phrase_repeats():
    text = FILL * 2 + "first we fix the ads, then we fix the ads landing page and then we measure everything. " + FILL
    spec = {"kind": "steps", "from": "first we fix", "to": "measure everything", "to_end": True,
            "items": ["fix the ads", "fix the ads landing page", "measure everything"]}
    out, errors, words = run([spec], text)
    assert errors == []
    first, second, third = (i["at"] for i in out[0]["items"])
    assert first == at(words, "fix the ads", 0) and second == at(words, "fix the ads", 1) and first < second < third
    # the same phrase twice picks the first, then the second occurrence
    twice = {**spec, "items": ["fix the ads", "fix the ads"]}
    out, errors, words = run([twice], text)
    assert errors == [] and [i["at"] for i in out[0]["items"]] == [at(words, "fix the ads", 0), at(words, "fix the ads", 1)]


def test_an_item_not_said_in_its_window_is_refused():
    spec = {**CYCLE_SPEC, "items": ["she gets anxious", "she avoids", "the moon landing", "a little relief"]}
    out, errors, _ = run([spec], CYCLE_TEXT)
    assert out == []
    [e] = errors
    assert '"the moon landing"' in e and "item 3" in e and " s" in e and "between" in e
    # said, but outside the window
    late = {**CYCLE_SPEC, "to": "you step in", "items": ["she gets anxious", "she avoids", "a little relief"]}
    out, errors, _ = run([late], CYCLE_TEXT)
    assert out == [] and any('"a little relief"' in e and "between" in e for e in errors)


def test_counts_and_lengths_are_enforced():
    def bad(spec, text=CYCLE_TEXT):
        out, errors, _ = run([spec], text)
        assert out == []
        return " ".join(errors)

    assert "3 to 6 items" in bad({**CYCLE_SPEC, "items": ["she gets anxious", "she avoids"]})
    assert "2 to 6 items" in bad({"kind": "checklist", "from": "okay so", "to": "a while", "to_end": True,
                                  "items": ["okay"] * 7})
    assert "2 to 5 items" in bad({**CYCLE_SPEC, "kind": "steps", "items": ["she gets anxious"] * 6})
    short = {**CYCLE_SPEC, "to": "she avoids", "to_end": False, "items": ["she gets anxious", "she avoids", "you step in"]}
    assert "lasts" in bad({**short, "to": "you step in"}) and "2.5 to 8 s" in bad({**short, "to": "you step in"})
    long_text = "go " + "and then something else happens here ok " * 4 + "end now please. " + FILL
    assert "lasts 11" in bad({"kind": "quote", "from": "go", "to": "end now please", "to_end": True, "hold": 0.0,
                              "line": "go"}, long_text)


def kit_job(tmp_path, monkeypatch, video, text, duration=None):
    """A job with a cut of `text` (a word every 0.35 s), a cached B-roll clip and a kit; the returned function
    writes plan.json and gives the job."""
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    job = Job("acme", Path("take1.mp4"))
    put_in_raw()
    job.work.mkdir(parents=True)
    words = make_words(text)
    (job.work / "cut.json").write_text(json.dumps({"words": words, "duration": duration or words[-1]["end"] + 0.3}), encoding="utf-8")
    job.broll_cache.mkdir(parents=True)
    shutil.copy(video(10, 360, 640), job.broll_cache / "pexels-1234567.mp4")
    kit = job.brand / "kit.json"
    kit.parent.mkdir(parents=True)
    kit.write_text(json.dumps({"approved": False, "lockup": {
        "callout": "For desk workers", "offer": ["Six weeks to moving freely"], "detail": None, "button": "Learn more"}}),
        encoding="utf-8")

    def write(**plan_json):
        (job.work / "plan.json").write_text(json.dumps({"lockup": "kit", **plan_json}), encoding="utf-8")
        return job

    write.words = words
    return write


@pytest.fixture
def setup(tmp_path, monkeypatch, video):
    """A 12-ish s cut with a cycle sentence in the middle and room before and after."""
    return kit_job(tmp_path, monkeypatch, video, FILL + CYCLE + FILL)


def errors_of(job):
    with pytest.raises(plan.PlanError) as err:
        plan.load(job)
    return err.value.errors


LONG = ("start. " + "talk " * 8 + "one two three four five six seven eight nine ten. " + "talk " * 40 +
        "alpha bravo charlie delta echo foxtrot golf hotel india juliet. " + "talk " * 40 +
        "kilo lima mike november oscar papa quebec romeo sierra tango. " + "talk " * 40 +
        "uniform victor whiskey xray yankee zulu again and again until done. " + "talk " * 40 +
        "one more graphic goes right here for the count of five. " + "talk " * 6)


def g(first, last, **kw):
    return {"kind": "checklist", "from": first, "to": last, "to_end": True, "hold": 0.3,
            "items": [first.split()[0], last.split()[-1]], **kw}


@pytest.fixture
def long_job(tmp_path, monkeypatch, video):
    return kit_job(tmp_path, monkeypatch, video, LONG)


def test_room_rules(long_job, tmp_path, monkeypatch, video):
    a = g("one two three", "six seven eight")
    # overlapping another graphic, B-roll, a heading
    job = long_job(graphics=[a, g("two three four", "seven eight nine")])
    assert any("overlap" in e and "Graphic 1" in e and "Graphic 2" in e for e in errors_of(job))
    job = long_job(graphics=[a], broll=[{"clip": "pexels-1234567", "from": "two three", "to": "five six", "why": "x"}])
    assert any("overlaps B-roll 1" in e for e in errors_of(job))
    job = long_job(graphics=[a], headings=[{"lines": ["four five six seven"], "from": "four five six", "to": "seven eight"}])
    assert any("overlaps heading 1" in e for e in errors_of(job))
    # inside the lockup's first 2.5 s
    early = {"kind": "checklist", "from": "talk talk", "to": "six seven", "to_end": True, "items": ["talk", "six"]}
    assert any("lockup" in e for e in errors_of(long_job(graphics=[early])))


def test_a_graphic_may_not_run_into_the_end_card(tmp_path, monkeypatch, video):
    words = make_words(LONG)
    last = [w for w in words if w["text"].strip(".") == "five"][-1]
    job = kit_job(tmp_path, monkeypatch, video, LONG, duration=round(last["end"] + 0.05, 3))
    errs = errors_of(job(graphics=[g("graphic goes right here", "count of five")]))
    assert any("end card" in e and "Graphic 1" in e for e in errs)


def test_a_fifth_graphic_is_refused(long_job):
    four = [g("one two three", "six seven eight"), g("alpha bravo charlie", "foxtrot golf hotel"),
            g("kilo lima mike", "papa quebec romeo"), g("uniform victor whiskey", "again and again")]
    plan.load(long_job(graphics=four))
    errs = errors_of(long_job(graphics=[*four, g("graphic goes right", "count of five")]))
    assert any("at most 4 graphics" in e and "has 5" in e for e in errs)


def spaced(tmp_path, monkeypatch, video, talks):
    text = "talk " * 8 + "alpha beta gamma delta epsilon zeta eta theta. " + "talk " * talks + \
           "iota kappa lambda mu nu xi omicron pi. " + "talk " * 10
    return kit_job(tmp_path, monkeypatch, video, text)


def pair(job):
    return job(graphics=[{"kind": "checklist", "from": "alpha beta gamma", "to": "zeta eta theta", "to_end": True,
                          "hold": 0.3, "items": ["alpha", "theta"]},
                         {"kind": "checklist", "from": "iota kappa lambda", "to": "xi omicron pi", "to_end": True,
                          "hold": 0.3, "items": ["iota", "pi"]}])


def test_two_graphics_ten_seconds_apart_are_refused(tmp_path, monkeypatch, video):
    job = spaced(tmp_path, monkeypatch, video, 21)  # starts 10.15 s apart
    assert any("Only one graphic may start in any 12 s" in e for e in errors_of(pair(job)))


def test_two_graphics_twelve_seconds_apart_are_fine(tmp_path, monkeypatch, video):
    job = spaced(tmp_path, monkeypatch, video, 27)  # starts 12.25 s apart
    assert len(plan.load(pair(job))["graphics"]) == 2


@pytest.mark.parametrize("shown,said,ok", [
    ("$7,500", "seven thousand five hundred dollars", True),
    ("$7,500", "7500 dollars", True),
    ("$7,500", "seven thousand dollars", False),
    ("90%", "ninety percent", True),
    ("90%", "ninety days", False),
    ("3x", "three times", True),
    ("30", "thirty appointments", True),
    ("R1.5m", "one point five", False),
])
def test_a_stat_must_be_said(shown, said, ok):
    text = FILL * 2 + f"we got you {said} in the end. " + FILL
    out, errors, _ = run([{"kind": "stat", "from": "we got you", "to": "in the end", "to_end": True,
                           "hold": 0.6, "value": shown}], text)
    assert bool(out) is ok
    assert (errors == []) is ok
    if not ok:
        assert shown in errors[0] and "between" in errors[0]


def test_a_stat_lands_when_its_number_is_said_and_takes_a_kept_label():
    text = FILL * 2 + "in about ninety days you will see it happen for real. " + FILL
    out, errors, words = run([{"kind": "stat", "from": "in about ninety days", "to": "for real", "to_end": True,
                               "hold": 0.4, "value": "90", "label": "you will see it"}], text)
    assert errors == [] and out[0]["value_at"] == at(words, "ninety") and out[0]["label"]["text"] == "you will see it"
    bad, errors, _ = run([{"kind": "stat", "from": "in about ninety days", "to": "for real", "to_end": True,
                           "hold": 0.4, "value": "90", "label": "free money"}], text)
    assert bad == [] and "free money" in errors[0]
    for shown in ("ninety", "", None):
        assert run([{"kind": "stat", "from": "in about ninety days", "to": "for real", "to_end": True,
                     "hold": 0.4, "value": shown}], text)[0] == []


VERSUS_TEXT = (FILL * 2 + "not the old way and not the slow way but the quick way that works every time. " + FILL)
VERSUS = {"kind": "versus", "from": "not the old way", "to": "works every time", "to_end": True, "hold": 0.5,
          "left": ["the old way", "the slow way"], "right": ["the quick way"], "turn": "but",
          "left_header": "not", "right_header": "works every time"}


def test_versus_turn_and_headers():
    out, errors, words = run([VERSUS], VERSUS_TEXT)
    assert errors == []
    [v] = out
    assert v["turn"]["at"] == at(words, "but")
    assert [i["text"] for i in v["left"]] == ["the old way", "the slow way"]
    assert v["right"][0]["at"] == at(words, "the quick way")
    assert v["left_header"]["text"] == "not" and v["right_header"]["text"] == "works every time"
    no_turn = {k: x for k, x in VERSUS.items() if k != "turn"}
    out, errors, _ = run([no_turn], VERSUS_TEXT)
    assert out == [] and any("turn" in e for e in errors)
    # headers are the speaker's words like any item
    out, errors, _ = run([{**VERSUS, "left_header": "Before"}], VERSUS_TEXT)
    assert out == [] and any('"Before"' in e for e in errors)
    # the strike lands inside the window, after the losing side
    out, errors, _ = run([{**VERSUS, "turn": "every time"}], VERSUS_TEXT)
    assert out == [] and errors
    out, errors, _ = run([{**VERSUS, "left": ["the old way"] * 5}], VERSUS_TEXT)
    assert out == [] and any("1 to 4" in e for e in errors)


def test_a_quote_takes_one_line_and_an_accent_from_it():
    text = FILL * 2 + "this is the one thing that matters most to you. " + FILL
    spec = {"kind": "quote", "from": "this is the one thing", "to": "matters most to you", "to_end": True,
            "hold": 0.4, "line": "the one thing that matters most", "accent": "matters"}
    out, errors, _ = run([spec], text)
    assert errors == [] and out[0]["line"]["text"] == "the one thing that matters most" and out[0]["accent"] == "matters"
    assert run([{**spec, "accent": "money"}], text)[0] == []


def test_unknown_kinds_keys_and_grounds_are_named():
    out, errors, _ = run([{**CYCLE_SPEC, "kind": "pie"}, {**CYCLE_SPEC, "ground": "red", "colour": "x"}], CYCLE_TEXT)
    assert out == []
    text = " ".join(errors)
    assert '"pie"' in text and '"colour"' in text and '"ground"' in text


def test_twenty_seconds_with_nothing_to_show_warns(tmp_path, monkeypatch, video):
    text = "start here " + "talk " * 8 + "the lamp shines on the desk " + "talk " * 4 + "more " * 70 + "the end of it all now"
    job = kit_job(tmp_path, monkeypatch, video, text)
    r = plan.load(job(broll=[{"clip": "pexels-1234567", "from": "the lamp", "to": "on the desk", "why": "lamp"}]))
    [warning] = r["warnings"]
    assert "nothing shows" in warning and " s" in warning
    covered = [(b["start"], b["end"]) for b in r["broll"]]
    assert covered and f"{covered[0][1]:.1f}" in warning


def test_sounds_flash_and_offset_defaults(setup):
    r = plan.load(setup())
    assert (r["sounds"], r["flash"], r["sfx_offset_db"], r["graphics"], r["warnings"]) == (True, True, 0.0, [], [])
    assert plan.load(setup(sfx_offset_db=9))["sfx_offset_db"] == 6
    assert plan.load(setup(sfx_offset_db=-20))["sfx_offset_db"] == -12
    r = plan.load(setup(sounds=False, flash=False))
    assert (r["sounds"], r["flash"]) == (False, False)
    assert any('"sounds"' in e for e in errors_of(setup(sounds="no")))


def test_load_returns_resolved_graphics_without_labels(setup):
    r = plan.load(setup(graphics=[CYCLE_SPEC | {"title": "she gets anxious", "ground": "paper", "sfx": False}]))
    [g1] = r["graphics"]
    assert "_label" not in g1 and g1["ground"] == "paper" and g1["sfx"] is False and g1["title"]["text"] == "she gets anxious"
    assert "graphic cycle" in plan.format_table(r, 20.0)


def test_the_lockup_and_the_end_card_are_not_idle_time(tmp_path, monkeypatch, video):
    # 22 s of cut: more than 20 s in all, but only 19.3 s between the lockup and the end card
    job = kit_job(tmp_path, monkeypatch, video, "talk " * 62)
    assert plan.load(job())["warnings"] == []


def test_the_warning_names_the_stretch_between_lockup_and_card(tmp_path, monkeypatch, video):
    job = kit_job(tmp_path, monkeypatch, video, "talk " * 70)
    [w] = plan.load(job())["warnings"]
    assert w.startswith("From 2.5 to 24.") and "nothing shows" in w


def test_sfx_is_read_on_broll_and_headings_and_must_be_a_bool(setup):
    heading = {"lines": ["she gets anxious"], "from": "she gets anxious", "to": "she avoids"}
    broll = {"clip": "pexels-1234567", "from": "a little relief", "to": "relief", "to_end": True, "why": "x"}
    r = plan.load(setup(headings=[heading], broll=[broll]))
    assert r["headings"][0]["sfx"] is True and r["broll"][0]["sfx"] is True
    r = plan.load(setup(headings=[{**heading, "sfx": False}], broll=[{**broll, "sfx": False}]))
    assert r["headings"][0]["sfx"] is False and r["broll"][0]["sfx"] is False
    errs = errors_of(setup(headings=[{**heading, "sfx": "no"}], broll=[{**broll, "sfx": 0}]))
    assert 'Heading 1: "sfx" is true or false.' in errs and 'B-roll 1: "sfx" is true or false.' in errs


def test_a_stat_is_read_from_symbol_bearing_kept_words():
    for shown, said in [("$7,500", "$7,500"), ("R5,000", "R5,000"), ("£300", "£300"),
                        ("€1,200", "€1,200"), ("90%", "90%"), ("$7,500", "7 thousand five hundred dollars")]:
        text = FILL * 2 + f"we got you {said} in the end. " + FILL
        out, errors, _ = run([{"kind": "stat", "from": "we got you", "to": "in the end", "to_end": True,
                               "hold": 0.6, "value": shown}], text)
        assert errors == [] and out, (shown, said)


def test_a_json_number_is_a_stat_value_and_a_bad_kind_is_refused():
    text = FILL * 2 + "we got you thirty calls in the end. " + FILL
    spec = {"kind": "stat", "from": "we got you", "to": "in the end", "to_end": True, "hold": 0.6, "value": 30}
    out, errors, _ = run([spec], text)
    assert errors == [] and out[0]["value"] == "30"
    for kind in ([], {}, None, 3):
        out, errors, _ = run([{**spec, "kind": kind}], text)
        assert out == [] and "kind" in errors[0]


def test_a_stat_is_not_passed_by_numbers_that_only_run_together():
    for said in ("five hundred, 3 clients", "three hundred. 5 clients", "12 hundred 5", "3 thousand 5"):
        merged = {"five hundred, 3 clients": "503", "three hundred. 5 clients": "305", "12 hundred 5": "1205",
                  "3 thousand 5": "3005"}[said]
        text = FILL * 2 + f"we got you {said} in the end. " + FILL
        out, errors, _ = run([{"kind": "stat", "from": "we got you", "to": "in the end", "to_end": True,
                               "hold": 0.6, "value": merged}], text)
        assert out == [] and errors, said


def test_a_stat_unit_must_be_said_with_its_number():
    text = FILL * 2 + "we booked 30 calls last month and cut the cost by ten percent in the end. " + FILL
    out, errors, _ = run([{"kind": "stat", "from": "we booked", "to": "in the end", "to_end": True,
                           "hold": 0.6, "value": "30%", "label": "calls"}], text)
    assert out == [] and "right after the number" in errors[0] and '"percent"' in errors[0]
    # said with its number, a unit passes, whether it is a word or a symbol
    for said in ("30 percent of calls", "30% of calls"):
        text = FILL * 2 + f"we booked {said} in the end. " + FILL
        out, errors, _ = run([{"kind": "stat", "from": "we booked", "to": "in the end", "to_end": True,
                               "hold": 0.6, "value": "30%"}], text)
        assert errors == [] and out, said
    # a pause between the number and the unit breaks the pair
    text = FILL * 2 + "we booked 30, percent of calls in the end. " + FILL
    out, errors, _ = run([{"kind": "stat", "from": "we booked", "to": "in the end", "to_end": True,
                           "hold": 0.6, "value": "30%"}], text)
    assert out == [] and errors


@pytest.mark.parametrize("item,said,ok", [
    ("$500 off", "500 off", False),
    ("$500 off", "500 dollars off", True),
    ("$500 off", "$500 off", True),
    ("€500 OFF!", "500 off", False),
    ("€500 OFF!", "500 euros off", True),
    ("R500 off", "500 off", False),
    ("R500 off", "500 rand off", True),
])
def test_an_item_currency_must_be_said_with_its_number(item, said, ok):
    text = FILL * 2 + f"alpha beta {said} and so on for a good while gamma delta. " + FILL
    out, errors, _ = run([{"kind": "checklist", "from": "alpha beta", "to": "gamma delta", "to_end": True,
                           "hold": 0.4, "items": ["alpha beta", item]}], text)
    assert bool(out) is ok and (errors == []) is ok


@pytest.mark.parametrize("length,ok", [(2.5, True), (8.0, True), (2.49, False), (8.01, False)])
def test_graphic_lengths_at_the_limits(length, ok):
    words = [{"text": "alpha", "start": 10.0, "end": 10.3, "src": 10.0},
             {"text": "beta", "start": 10.5, "end": 10.8, "src": 10.5},
             {"text": "gamma", "start": round(10.0 + length - 0.4, 3), "end": round(10.0 + length - 0.1, 3), "src": 0},
             {"text": "omega", "start": round(10.0 + length, 3), "end": round(10.3 + length, 3), "src": 0}]
    kept = plan.Kept(words)
    errors: list[str] = []
    out = graphic_plan.resolve([{"kind": "checklist", "from": "alpha", "to": "omega", "items": ["alpha", "gamma"]}],
                               kept, {"duration": 30.0}, errors)
    assert bool(out) is ok and (errors == []) is ok
    if ok:
        assert out[0]["end"] - out[0]["start"] == pytest.approx(length)
    else:
        assert "A graphic lasts 2.5 to 8 s" in errors[0]
