import pytest
from conftest import make_speech

from adeditor import media, mix

# A voice of tones with pauses between them, as a take has between lines.
PATTERN = [("s", 0.6)] + [("w", 1.3), ("s", 0.5), ("w", 0.9), ("s", 0.9)] * 5 + [("s", 0.4)]


def tone_words(pattern):
    words, t = [], 0.0
    for kind, dur in pattern:
        if kind == "w":
            words.append({"text": "w", "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur
    return words


def pink(tmp_path, seconds, name="bed.mp3"):
    out = tmp_path / name
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
               f"anoisesrc=color=pink:amplitude=0.3:duration={seconds}:sample_rate=48000:seed=1",
               "-ac", "2", "-c:a", "libmp3lame", "-q:a", "2", str(out)])
    return out


@pytest.fixture
def take(tmp_path, video):
    voice = make_speech(tmp_path, PATTERN)
    speech = sum(d for _, d in PATTERN)
    duration = round(speech - 0.2 + 3.2, 3)
    silent = video(duration, 320, 240)
    return voice, silent, speech, duration


def test_bed_sits_about_15_db_under(tmp_path, take):
    voice, silent, speech, duration = take
    out = tmp_path / "ad.mp4"
    got = mix.mix(voice, pink(tmp_path, 40), silent, out, duration, speech - 0.2, None,
                  stems_dir=tmp_path / "mix", words=tone_words(PATTERN))
    under = mix.bed_under_voice(got["voice_stem"], got["bed_stem"], tone_words(PATTERN))
    assert under["median_db"] == pytest.approx(15, abs=0.5)
    assert got["bed_median_db"] == under["median_db"]
    assert under["by_30s"] and all(12 <= v <= 18 for v in under["by_30s"] if v is not None)
    lufs, peak = mix.measure(out)
    assert lufs == pytest.approx(-14, abs=1) and peak <= -1.5 + 0.2
    assert (got["lufs"], got["true_peak"]) == (lufs, peak)
    probe = media.probe(out)
    assert probe.has_audio and probe.duration == pytest.approx(duration, abs=0.05)
    assert got["voice_stem"] == tmp_path / "mix" / "voice.wav" and got["bed_stem"] == tmp_path / "mix" / "bed.wav"


def test_no_bed_still_levels(tmp_path, take):
    voice, silent, speech, duration = take
    out = tmp_path / "ad.mp4"
    got = mix.mix(voice, None, silent, out, duration, speech - 0.2, None, stems_dir=tmp_path / "mix")
    lufs, peak = mix.measure(out)
    assert lufs == pytest.approx(-14, abs=1) and peak <= -1.3
    assert got["bed_stem"] is None and got["bed_median_db"] is None
    assert got["voice_stem"].is_file()


def test_a_short_bed_is_extended_to_the_whole_ad(tmp_path):
    voice = make_speech(tmp_path, [("s", 1.0), ("w", 1.0), ("s", 28.0)])
    stems = mix.stems(voice, pink(tmp_path, 20), 30.0, 26.8, 72, out_dir=tmp_path / "mix")
    length = float(media.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              str(stems["bed"])]).stdout)
    assert length == pytest.approx(30.0, abs=0.05)
    # the bed is still playing at 25 s, after the 20 s file ran out (its fade out starts at 28.6 s)
    level = mix.momentary(stems["bed"])
    assert max(m for t, m in level if 24.5 <= t <= 25.5) > -40


def test_the_loop_point_sits_on_a_bar_line_in_phase():
    bar = 60 / 72 * 4
    spans = mix.bed_spans(165.04, 172.2, 72)
    assert len(spans) == 2
    (a0, a1), (b0, b1) = spans
    assert a0 == 0 and b1 is None
    assert a1 == pytest.approx(round(a1 / bar) * bar, abs=1e-3)  # pass 1 ends on a bar line
    assert a1 <= 165.04 - 8  # and before the bed's own ending
    jump = (a1 - 4) - b0  # the second pass enters at the same phase as the crossfade's start
    assert jump == pytest.approx(round(jump / bar) * bar, abs=1e-3) and jump > 0
    assert a1 + (165.04 - b0) - 4 >= 172.2


def test_with_no_bpm_the_loop_point_is_8_s_from_the_end():
    spans = mix.bed_spans(60.0, 70.0, None)
    (a0, a1), (b0, b1) = spans
    assert (a0, a1, b1) == (0, 52.0, None)
    assert a1 + (60.0 - b0) - 4 >= 70.0


def test_a_long_ad_loops_more_than_once():
    spans = mix.bed_spans(30.0, 100.0, 120)
    assert len(spans) > 2
    total = sum((b if b is not None else 30.0) - a for a, b in spans) - 4 * (len(spans) - 1)
    assert total >= 100.0
    assert spans[0][0] == 0 and spans[-1][1] is None


def test_a_bed_long_enough_is_not_looped():
    assert mix.bed_spans(240.0, 140.0, 72) == [(0.0, None)]


def test_a_bed_too_short_to_loop_says_so():
    with pytest.raises(ValueError, match="too short"):
        mix.bed_spans(9.0, 60.0, 72)


def test_silent_bed_readings_are_ignored_and_an_all_silent_bed_is_not_measurable(monkeypatch):
    inf = float("inf")
    words = [{"text": "w", "start": 0.0, "end": 1.0}]
    voice = [(0.2, -20.0), (0.4, -20.0), (0.6, -20.0)]

    def readings(bed):
        return lambda path: voice if path == "voice" else bed

    monkeypatch.setattr(mix, "momentary", readings([(0.2, -inf), (0.4, -35.0), (0.6, -35.0)]))
    under = mix.bed_under_voice("voice", "bed", words)
    assert under["median_db"] == 15.0 and under["by_30s"] == [15.0]

    monkeypatch.setattr(mix, "momentary", readings([(t, -inf) for t, _ in voice]))
    assert mix.bed_under_voice("voice", "bed", words) == {"median_db": None, "by_30s": []}


def brown(tmp_path, seconds, amplitude, name="bed2.mp3"):
    out = tmp_path / name
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
               f"anoisesrc=color=brown:amplitude={amplitude}:duration={seconds}:sample_rate=48000:seed=7",
               "-af", "tremolo=f=0.4:d=0.6", "-ac", "2", "-c:a", "libmp3lame", "-q:a", "2", str(out)])
    return out


@pytest.mark.parametrize("make", [lambda p: pink(p, 40), lambda p: brown(p, 40, 0.9), lambda p: brown(p, 40, 0.05)])
def test_the_bed_is_levelled_to_15_db_under_whatever_its_loudness(tmp_path, take, make):
    voice, silent, speech, duration = take
    got = mix.mix(voice, make(tmp_path), silent, tmp_path / "ad.mp4", duration, speech - 0.2, None,
                  stems_dir=tmp_path / "mix", words=tone_words(PATTERN))
    assert got["bed_median_db"] == pytest.approx(15, abs=0.5)


@pytest.mark.parametrize("offset, expected", [(-3, 18), (3, 12)])
def test_the_offset_moves_the_bed_from_15_quieter_when_negative(tmp_path, take, offset, expected):
    voice, silent, speech, duration = take
    got = mix.mix(voice, pink(tmp_path, 40), silent, tmp_path / "ad.mp4", duration, speech - 0.2, None, offset,
                  stems_dir=tmp_path / "mix", words=tone_words(PATTERN))
    assert got["bed_median_db"] == pytest.approx(expected, abs=0.6)


def _spy(monkeypatch, readings):
    gains, seq = [], iter(readings)
    real = mix._bed_chain
    monkeypatch.setattr(mix, "_bed_chain", lambda spans, d, e, gain: gains.append(gain) or real(spans, d, e, gain))
    monkeypatch.setattr(mix, "bed_under_voice", lambda v, b, w: {"median_db": next(seq), "by_30s": []})
    return gains


def test_a_nearly_silent_bed_is_lifted_by_at_most_12_db_and_says_so(tmp_path, take, monkeypatch):
    voice, silent, speech, duration = take
    gains = _spy(monkeypatch, [45.0, 31.0])
    got = mix.stems(voice, pink(tmp_path, 40), duration, speech - 0.2, None, out_dir=tmp_path / "mix",
                    words=tone_words(PATTERN))
    assert gains == [0.0, 12.0] and got["capped"] is True and got["median_db"] == 31.0
    gains = _spy(monkeypatch, [20.0, 15.0])
    got = mix.stems(voice, pink(tmp_path, 40), duration, speech - 0.2, None, 1.0, out_dir=tmp_path / "mix",
                    words=tone_words(PATTERN))
    assert gains == [0.0, 6.0] and got["capped"] is False and got["median_db"] == 15.0


def test_a_bed_that_cannot_be_measured_gets_only_the_offset(tmp_path, take, monkeypatch):
    voice, silent, speech, duration = take
    gains = _spy(monkeypatch, [None])
    got = mix.stems(voice, pink(tmp_path, 40), duration, speech - 0.2, None, 2.0, out_dir=tmp_path / "mix",
                    words=tone_words(PATTERN))
    assert gains == [0.0, 2.0] and got["median_db"] is None and got["capped"] is False
    assert got["bed"].is_file()


def test_mix_measures_the_bed_once_per_ratio(tmp_path, take, monkeypatch):
    voice, silent, speech, duration = take
    calls = []
    real = mix.bed_under_voice
    monkeypatch.setattr(mix, "bed_under_voice", lambda *a: calls.append(1) or real(*a))
    got = mix.mix(voice, pink(tmp_path, 40), silent, tmp_path / "ad.mp4", duration, speech - 0.2, None,
                  stems_dir=tmp_path / "mix", words=tone_words(PATTERN))
    assert len(calls) == 2 and got["bed_median_db"] == pytest.approx(15, abs=0.5)  # level, then the final reading
