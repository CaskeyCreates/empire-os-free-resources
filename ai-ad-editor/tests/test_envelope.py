import math

from adeditor import envelope

TONE_RMS_DB = -20 + 20 * math.log10(1 / math.sqrt(2))  # -20 dBFS peak sine


def test_envelope_reads_tone_and_silence(speech):
    env = envelope.envelope_db(_f32(speech([("s", 0.5), ("w", 1.0), ("s", 0.5)])))
    assert len(env) == 200
    assert all(v < -60 for v in env[0:50])
    assert all(abs(v - TONE_RMS_DB) < 1.5 for v in env[55:145])
    floor, line = envelope.floor_and_line(env)
    assert floor < -60 and line == floor + 12
    assert max(env[0:50]) < line < min(env[55:145])


def _f32(wav):
    from adeditor import media
    return media.extract_audio(wav, wav.with_name("f32.wav"))


def test_padding_before_a_late_audio_stream_is_not_the_floor():
    room, voice = [-75.0] * 40, [-23.0] * 60
    floor, _ = envelope.floor_and_line([-100.0] * 50 + room + voice)
    assert abs(floor - -75) < 0.01
    # A gated mic's digital silence between words still counts.
    floor, _ = envelope.floor_and_line([-100.0] * 10 + voice + [-100.0] * 30 + voice)
    assert floor == -100
