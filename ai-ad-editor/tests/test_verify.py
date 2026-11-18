from adeditor import verify


def said(text, start, key="text", length=0.3, gap=0.05):
    """Words one after another from `start`, each `length` long and `gap` apart."""
    out, t = [], start
    for word in text.split():
        out.append({key: word, "start": round(t, 3), "end": round(t + length, 3)})
        t += length + gap
    return out


def test_normalise_equates_forms():
    assert verify.normalise("90-day") == verify.normalise("ninety day")
    assert verify.normalise("you're") == verify.normalise("youre")
    assert verify.normalise("20%") == verify.normalise("twenty percent")


def test_normalise_cleans_case_quotes_and_punctuation():
    assert verify.normalise("\u201cDon\u2019t,\u201d she SAID!") == ["dont", "she", "said"]
    assert verify.normalise("ninety-nine") == verify.normalise("99") == ["99"]
    assert verify.normalise("one hundred") == verify.normalise("100") == ["100"]
    assert verify.normalise("'Hello'") == ["hello"]


def test_the_same_words_all_match():
    kept = said("we fix knees and backs", 1.0)
    report = verify.compare(kept, said("We fix knees, and backs.", 1.0, key="word"), [0.5])
    assert (report.matched, report.total, report.near, report.far) == (5, 5, [], [])


def test_transcriber_noise_far_from_every_join_is_far():
    kept = said("stretch along the floor", 10.0)
    heard = said("stretch along a floor", 10.0, key="word")
    report = verify.compare(kept, heard, [5.0, 16.0])  # "the" ends at 11.0, 5 s from the nearest join
    assert report.near == []
    assert report.far == [{"kind": "changed", "kept": "the", "heard": "a", "t": 10.7, "to_join": 5.0}]
    assert (report.matched, report.total) == (3, 4)


def test_missing_word_next_to_a_join_is_near():
    kept = said("we fix knees fast", 1.0)  # "knees" runs 1.7 to 2.0
    heard = said("we fix", 1.0, key="word") + said("fast", 2.05, key="word")
    report = verify.compare(kept, heard, [2.3])
    assert report.far == []
    assert report.near == [{"kind": "missing", "kept": "knees", "heard": "", "t": 1.7, "to_join": 0.3}]


def test_an_extra_word_at_a_join_is_near():
    kept = said("we fix knees", 1.0)
    heard = said("we fix um knees", 1.0, key="word")
    report = verify.compare(kept, heard, [1.75])
    assert [(d["kind"], d["heard"]) for d in report.near] == [("extra", "um")]
    assert report.matched == report.total == 3


def test_an_extra_struck_word_is_near_only_where_it_was_cut():
    kept = said("we really want the best care", 1.0)  # "the" at 2.05, "best" at 2.4
    heard = said("we really want the the best care", 1.0, key="word")
    struck = [{"text": "The,", "src": 3.0, "t": 2.2}]  # a struck "the" was cut out right here
    report = verify.compare(kept, heard, [], struck=struck)
    assert [(d["kind"], d["heard"]) for d in report.near] == [("extra", "the")] and report.far == []
    # The same struck word cut out 7 s away: an extra "the" here is transcriber noise.
    report = verify.compare(kept, heard, [], struck=[{**struck[0], "t": 9.2}])
    assert report.near == [] and report.far[0]["kind"] == "extra"
    assert verify.compare(kept, heard, []).far[0]["kind"] == "extra"  # with nothing struck, it's noise


def test_no_joins_means_nothing_is_near():
    report = verify.compare(said("we fix knees", 1.0), said("we fix", 1.0, key="word"), [])
    assert report.near == [] and report.far[0]["to_join"] is None


def test_explain_names_the_word_the_time_and_the_line():
    kept = said("Book your first visit. We fix knees and backs.", 70.0)
    diff = {"kind": "changed", "kept": "knees,", "heard": "needs", "t": 72.1, "to_join": 0.2}
    text = verify.explain(diff, kept)
    assert text.startswith("The word 'knees' near 1:12 may be clipped (it was heard as 'needs')")
    assert text.endswith("it's in the line 'We fix knees and backs.'")
    assert "Book" not in text
    for dash in ("\u2014", "\u2013", "-" * 2):
        assert dash not in text
