import json

import pytest
import requests
from conftest import put_in_raw

from adeditor import cli, retakes, transcribe
from adeditor.paths import Job


def say(*parts, start=1.0, length=0.3, gap=0.05):
    """Words as the transcript has them, each `length` long and `gap` apart; a number between
    the strings is a pause of that many seconds instead of the gap."""
    words, t, pause = [], start, None
    for part in parts:
        if isinstance(part, (int, float)):
            pause = float(part)
            continue
        for text in part.split():
            if words:
                t += gap if pause is None else pause
            pause = None
            words.append({"word": text, "start": round(t, 3), "end": round(t + length, 3)})
            t += length
    return words


def texts(words):
    return [" ".join(w["word"] for w in words[s["from"]:s["to"] + 1]) for s in retakes.find(words)]


def kept(words):
    gone = set()
    for s in retakes.find(words):
        gone.update(range(s["from"], s["to"] + 1))
    return " ".join(w["word"] for i, w in enumerate(words) if i not in gone)


def test_fillers_are_found_and_real_words_are_not():
    said = ["Um,", "uh", "Hmm.", "uhm", "mm", "erm,", "ah", "Umm...", "er",
            "like", "you", "know", "umbrella", "a", "mhm", "Uh-huh", "her", "are"]
    words = [{"word": w, "start": i, "end": i + 0.5} for i, w in enumerate(said)]
    found = {words[i]["word"] for i in retakes.fillers(words)}
    assert found == {"Um,", "uh", "Hmm.", "uhm", "mm", "erm,", "ah", "Umm...", "er"}


def test_a_cut_off_word_is_struck_when_the_next_word_finishes_it():
    said = ["we", "pre-", "pre-qualified", "them", "well-known", "Co-", "owners", "re-", "do", "- "]
    words = [{"word": w, "start": i, "end": i + 0.5} for i, w in enumerate(said)]
    assert {words[i]["word"] for i in retakes.fillers(words)} == {"pre-"}


def test_lines_break_at_sentence_ends_and_pauses():
    words = say("one two.", "three four", 0.8, "five six", 3.5, "seven")
    assert retakes.lines(words) == [[0, 1], [2, 3], [4, 5], [6]]


def test_a_false_start_that_trails_off_and_restarts_is_a_retake():
    words = say("So if you're a solo contractor and you're,", 1.2,
                "so if you're a solo contractor and you're tired of chasing leads, this is for you.")
    found = retakes.find(words)
    assert texts(words) == ["So if you're a solo contractor and you're,"]
    assert "false start" in found[0]["why"]


def test_the_restart_is_cut_from_where_it_begins_again():
    words = say("So if you're a solo,", 1.0, "if you're a solo contractor, listen.")
    assert texts(words) == ["if you're a solo,"]
    assert kept(words) == "So if you're a solo contractor, listen."


def test_a_contraction_restarts_on_its_stem():
    words = say("If you're a,", 5.0, "If you want 30 homeowners on your calendar.")
    found = retakes.find(words)
    assert [(s["from"], s["to"], s["why"]) for s in found] == [(0, 2, "false start, said again after a pause")]


def test_no_restart_without_the_pause_or_the_shared_words():
    assert retakes.find(say("We build your funnel,", 1.0, "then we launch your ads.")) == []
    assert retakes.find(say("If you're a solo,", 0.4, "if not, a team.")) == []


def test_a_line_said_twice_keeps_the_later_take():
    words = say("We build your funnel and booking flow.", 0.4,
                "We build your funnel and your booking flow in seven days.")
    found = retakes.find(words)
    assert [(" ".join(w["word"] for w in words[s["from"]:s["to"] + 1]), s["why"]) for s in found] == [
        ("We build your funnel and booking flow.", "the same line, said again")]


def test_lines_that_only_open_alike_are_not_retakes():
    assert retakes.find(say("First, we build your funnel.", 0.4, "Next, we build your content.")) == []
    assert retakes.find(say("We build the funnel.", 0.4, "We build the ads.", 0.4, "We build the follow-up.")) == []
    assert retakes.find(say("We build your funnel in seven days.", 0.4, "Seven days.")) == []


def test_a_phrase_said_twice_straight_off_loses_its_first_copy():
    words = say("turn clicks into appointments instead of dead leads and we launch instead of dead leads "
                "and we launch targeted ads.")
    assert texts(words) == ["instead of dead leads and we launch"]
    assert kept(words) == "turn clicks into appointments instead of dead leads and we launch targeted ads."


def test_doubled_words_that_are_real_speech_stay():
    assert retakes.find(say("Market after market, month after month, one by one.")) == []


def test_three_attempts_keep_only_the_last():
    words = say("If you want 30 homeowners on your calendar,", 1.1,
                "if you want 30 qualified homeowners on your calendar,", 1.1,
                "if you want 30 qualified homeowners on your calendar every month, pay attention.")
    assert texts(words) == ["If you want 30 homeowners on your calendar,",
                            "if you want 30 qualified homeowners on your calendar,"]
    assert kept(words) == "if you want 30 qualified homeowners on your calendar every month, pay attention."


def test_describe_reads_plainly():
    words = say("So if you're a solo,", 1.0, "if you're a solo contractor, listen.")
    proposal = {"strikes": retakes.find(words), "fillers": [], "approved": False}
    out = retakes.describe(words, proposal)
    assert "Cut the first try at: 'if you're a solo,'" in out
    assert "—" not in out and "–" not in out and "-" * 2 not in out


def test_describe_lists_fillers_with_their_count():
    words = say("so um we uh build")
    out = retakes.describe(words, {"strikes": [], "fillers": retakes.fillers(words), "approved": False})
    assert "2 fillers" in out and "um" in out and "uh" in out


def test_write_proposal_writes_strikes_fillers_and_unapproved(tmp_path, monkeypatch):
    monkeypatch.setattr("adeditor.paths.ROOT", tmp_path)
    job = Job("acme", tmp_path / "raw" / "take1.mp4")
    words = say("So if you're a solo,", 1.0, "if you're a solo contractor, um listen.")
    retakes.write_proposal(job, words)
    data = json.loads((job.work / "strikes.json").read_text())
    assert data["approved"] is False
    assert data["fillers"] == [retakes.fillers(words)[0]]
    assert data["strikes"][0]["from"] == 1


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr("adeditor.paths.ROOT", tmp_path)
    job = Job("acme", tmp_path / "raw" / "take1.mp4")
    job.video.parent.mkdir()
    job.video.write_bytes(b"x")
    job.work.mkdir(parents=True)
    (job.work / "transcript.json").write_text(json.dumps({"words": say("one two three four five six")}))
    return job


def approve(job):
    return cli.main(["approve-cut", job.client, str(job.video)])


def write(job, strikes, fillers=()):
    (job.work / "strikes.json").write_text(json.dumps({"strikes": strikes, "fillers": list(fillers), "approved": False}))


def test_approve_sets_the_flag(job):
    write(job, [{"from": 0, "to": 1, "why": "x"}, {"from": 3, "to": 4, "why": "Claude: y"}])
    assert approve(job) == 0
    assert json.loads((job.work / "strikes.json").read_text())["approved"] is True


@pytest.mark.parametrize("strikes,fragment", [
    ([{"from": 0, "to": 2, "why": "x"}, {"from": 2, "to": 3, "why": "y"}], "overlap"),
    ([{"from": 4, "to": 9, "why": "x"}], "past"),
    ([{"from": 3, "to": 1, "why": "x"}], "before"),
])
def test_approve_refuses_bad_strikes(job, capsys, strikes, fragment):
    write(job, strikes)
    assert approve(job) == 1
    assert fragment in capsys.readouterr().err
    assert json.loads((job.work / "strikes.json").read_text())["approved"] is False


def test_approve_refuses_a_missing_proposal(job, capsys):
    assert approve(job) == 1
    assert "prepare" in capsys.readouterr().err


def test_prepare_command_prints_the_description(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("adeditor.paths.ROOT", tmp_path)
    brand = tmp_path / "clients" / "acme" / "brand"
    brand.mkdir(parents=True)
    (brand / "kit.json").write_text(json.dumps({"keyterms": ["Northbank"]}))
    words = say("So if you're a solo,", 1.0, "if you're a solo contractor, listen.")
    seen = {}

    def fake(job, keyterms):
        seen["keyterms"] = keyterms
        return words

    monkeypatch.setattr(transcribe, "prepare", fake)
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "take1.mp4").write_bytes(b"x")
    assert cli.main(["prepare", "acme", str(tmp_path / "raw" / "take1.mp4")]) == 0
    assert seen["keyterms"] == ["Northbank"]
    assert "Cut the first try at: 'if you're a solo,'" in capsys.readouterr().out


def test_prepare_command_stops_plainly_on_no_sound(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("adeditor.paths.ROOT", tmp_path)

    def silent(job, keyterms):
        raise transcribe.NoSound("This video has no sound we can hear.")

    monkeypatch.setattr(transcribe, "prepare", silent)
    put_in_raw(name="x.mp4")
    assert cli.main(["prepare", "acme", "x.mp4"]) == 1
    assert "no sound we can hear" in capsys.readouterr().err


def one_plain_line(capsys) -> str:
    err = capsys.readouterr().err
    assert "Traceback" not in err and len(err.strip().splitlines()) == 1, err
    return err


@pytest.mark.parametrize("error,fragment", [
    (transcribe.ScribeError('ElevenLabs Scribe returned HTTP 401: {"detail":\n "invalid_api_key"}'), "HTTP 401"),
    (requests.ConnectionError("HTTPSConnectionPool(host='api.elevenlabs.io'): Max retries exceeded"), "internet connection"),
    (requests.ReadTimeout("read timed out"), "too long"),
])
def test_prepare_command_stops_plainly_when_scribe_fails(tmp_path, monkeypatch, capsys, error, fragment):
    monkeypatch.setattr("adeditor.paths.ROOT", tmp_path)

    def fail(job, keyterms):
        raise error

    monkeypatch.setattr(transcribe, "prepare", fail)
    put_in_raw()
    assert cli.main(["prepare", "acme", "take1.mp4"]) == 1
    err = one_plain_line(capsys)
    assert fragment in err and "adeditor prepare acme" in err
    assert "HTTPSConnectionPool" not in err


def test_prepare_command_says_a_broken_kit_is_broken(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("adeditor.paths.ROOT", tmp_path)
    brand_dir = tmp_path / "clients" / "acme" / "brand"
    brand_dir.mkdir(parents=True)
    (brand_dir / "kit.json").write_text('{"keyterms": ["Northbank",]}')
    monkeypatch.setattr(transcribe, "prepare", lambda job, keyterms: pytest.fail("no transcription on a broken kit"))
    put_in_raw()
    assert cli.main(["prepare", "acme", "take1.mp4"]) == 1
    err = one_plain_line(capsys)
    assert "kit.json isn't valid JSON" in err and "line 1" in err and "client new" not in err


def test_approve_says_where_strikes_json_is_broken(job, capsys):
    (job.work / "strikes.json").write_text('{"strikes": [\n  {"from": 0, "to": 1,}\n], "fillers": []}')
    assert approve(job) == 1
    err = one_plain_line(capsys)
    assert "strikes.json isn't valid JSON" in err and "line 2" in err


@pytest.mark.parametrize("proposal,fragment", [
    ({"strikes": [{"start": 0, "end": 1}], "fillers": []}, '"from" and "to"'),
    ({"strikes": [{"from": "3", "to": 4, "why": "x"}], "fillers": []}, "word numbers without quotes"),
    ({"strikes": [{"from": 1.5, "to": 4, "why": "x"}], "fillers": []}, "word numbers without quotes"),
    ({"strikes": {"from": 0, "to": 1}, "fillers": []}, "should be a list"),
    ({"strikes": [], "fillers": ["4"]}, "fillers"),
    (["not", "an", "object"], "one JSON object"),
])
def test_approve_refuses_strikes_of_the_wrong_shape_plainly(job, capsys, proposal, fragment):
    (job.work / "strikes.json").write_text(json.dumps(proposal))
    assert approve(job) == 1
    assert fragment in one_plain_line(capsys)
