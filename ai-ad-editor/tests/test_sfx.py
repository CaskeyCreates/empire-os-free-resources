import subprocess
import wave

import numpy as np
import pytest

from adeditor import sfx

MANIFEST = {r: {"file": f"{r}.wav", "transient": 0.005, "peak100_db": -12.0} for r in sfx.LEVEL}


def ev(role, at, sfx_on=True):
    return {"role": role, "event": role, "at": at, "sfx": sfx_on}


def roles(cues):
    return [(c["role"], c["at"]) for c in cues]


def test_limits_and_priority():
    # two frames apart: the higher priority stays
    cues = sfx.place([ev("tick", 1.0), ev("cut", 1.0 + 2 / 30)], 10, -16, MANIFEST, 0)
    assert roles(cues) == [("cut", 1.067)]
    cues = sfx.place([ev("cut", 1.0), ev("tick", 1.0 + 2 / 30)], 10, -16, MANIFEST, 0)
    assert roles(cues) == [("cut", 1.0)]
    # a hit under a cut within 2 frames stays, either order
    both = sfx.place([ev("cut", 2.0), ev("hit", 2.0 + 2 / 30)], 10, -16, MANIFEST, 0)
    assert sorted(r for r, _ in roles(both)) == ["cut", "hit"]
    both = sfx.place([ev("hit", 2.0), ev("cut", 2.0 + 1 / 30)], 10, -16, MANIFEST, 0)
    assert sorted(r for r, _ in roles(both)) == ["cut", "hit"]
    # a tick under a cut does not layer
    assert roles(sfx.place([ev("cut", 2.0), ev("tick", 2.0 + 1 / 30)], 10, -16, MANIFEST, 0)) == [("cut", 2.0)]
    # a 4th moment in one second drops, and it is the weakest
    four = [ev("cut", 3.0), ev("hit", 3.3), ev("tick", 3.5), ev("whip", 3.8)]
    got = sfx.place(four, 10, -16, MANIFEST, 0)
    assert roles(got) == [("cut", 3.0), ("hit", 3.3), ("whip", 3.8)]
    # an event at duration minus 0.02 drops; sfx False drops
    assert sfx.place([ev("cut", 9.98), ev("cut", 5.0, sfx_on=False)], 10, -16, MANIFEST, 0) == []
    assert roles(sfx.place([ev("cut", 9.9)], 10, -16, MANIFEST, 0)) == [("cut", 9.9)]


def test_a_short_ad_dense_with_events_keeps_the_strongest():
    events = [ev(r, round(0.5 + i * 0.2, 3)) for i, r in enumerate(["tick", "tick", "cut", "tick", "hit", "tick", "whip",
                                                                   "tick", "cut", "tick", "push"] * 2)]
    events.append(ev("cut", 11.98))
    got = sfx.place(events, 12.0, -16, MANIFEST, 0)
    assert all(c["at"] <= 11.95 for c in got)
    for c in got:
        window = [d for d in got if c["at"] - 1 < d["at"] <= c["at"]]
        assert len(window) <= 3
    kept, gone = sfx.limit(events, 12.0)
    assert all("why" in g for g in gone) and len(kept) + len(gone) == len(events)
    over = [g for g in gone if g["why"] == "over three sounds in a second"]
    assert over  # the dense run did hit the cap
    for g in over:  # each dropped for the cap is no stronger than a kept moment in its second
        near = [k for k in kept if g["at"] - 1 < k["at"] <= g["at"] + 1]
        assert near and sfx.PRIORITY[g["role"]] <= max(sfx.PRIORITY[k["role"]] for k in near)


def test_gains_follow_the_voice_and_clamp():
    got = sfx.place([ev("cut", 1.0), ev("tick", 2.0)], 10, -16.0, MANIFEST, 0)
    # voice + level - peak100: -16 - 9 + 12 = -13 ; -16 - 16 + 12 = -20
    assert [c["gain_db"] for c in got] == [-13.0, -20.0] and not any(c["clamped"] for c in got)
    got = sfx.place([ev("cut", 1.0)], 10, -16.0, MANIFEST, 3.0)
    assert got[0]["gain_db"] == -10.0
    # a quiet voice and +6: gains clamp at +6 and say so
    quiet = {r: {**m, "peak100_db": -60.0} for r, m in MANIFEST.items()}
    got = sfx.place([ev("cut", 1.0), ev("tick", 2.0)], 10, -16.0, quiet, 6.0)
    assert [c["gain_db"] for c in got] == [6.0, 6.0] and all(c["clamped"] for c in got)
    got = sfx.place([ev("tick", 1.0)], 10, -90.0, MANIFEST, -12.0)
    assert got[0]["gain_db"] == -40.0 and got[0]["clamped"]


def test_rates_cycle():
    got = sfx.place([ev("tick", 1.0 + i) for i in range(5)] + [ev("cut", 0.5)], 20, -16, MANIFEST, 0)
    assert [c["rate"] for c in got if c["role"] == "tick"] == [0.97, 1.0, 1.03, 0.97, 1.0]
    assert [c["rate"] for c in got if c["role"] == "cut"] == [0.97]


def test_the_cue_starts_before_its_transient():
    [c] = sfx.place([ev("cut", 1.0)], 10, -16, {**MANIFEST, "cut": {**MANIFEST["cut"], "transient": 0.1}}, 0)
    assert c["start"] == pytest.approx(1.0 - 0.1 / 0.97, abs=0.001)
    [c] = sfx.place([ev("cut", 0.004)], 10, -16, MANIFEST, 0)
    assert c["start"] == 0.0


def tick_wav(path, rate=48000):
    n = int(0.3 * rate)
    t = np.arange(n) / rate
    x = np.zeros(n)
    x[int(0.005 * rate):] = 0.8 * np.sin(2 * np.pi * 2000 * t[: n - int(0.005 * rate)]) * np.exp(-t[: n - int(0.005 * rate)] * 40)
    x[int(0.005 * rate)] = 0.9  # the transient sample is the loudest
    data = (x * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(data.tobytes())


def read(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", "48000", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<f4").reshape(-1, 2)


def test_the_stem_puts_the_transient_on_the_time(tmp_path):
    tick_wav(tmp_path / "tick.wav")
    manifest = {"tick": {"file": "tick.wav", "path": str(tmp_path / "tick.wav"), "transient": 0.005, "peak100_db": -12.0}}
    cues = [{"role": "tick", "at": 2.0, "start": 1.995, "gain_db": -6.0, "rate": 1.0, "clamped": False}]
    out = sfx.stem(cues, manifest, 4.0, tmp_path / "effects.wav")
    x = read(out)
    assert x.shape[0] == pytest.approx(4.0 * 48000, abs=2) and x.shape[1] == 2
    peak_at = np.abs(x).max(axis=1).argmax() / 48000
    assert abs(peak_at - 2.0) <= 1 / 30
    assert abs(peak_at - 2.0) < 0.002
    assert np.abs(x[: int(1.9 * 48000)]).max() == 0
    assert np.abs(x).max() == pytest.approx(0.9 * 0.7071 * 10 ** (-6 / 20), abs=0.02)  # mono upmixed at -3 dB per channel
