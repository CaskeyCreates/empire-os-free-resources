import json
import math
import struct
import wave

import pytest

from adeditor import doctor, paths, sounds


def click(path, lead=0.12, total=0.5, rate=48000):
    """A 1 kHz burst after `lead` s of silence."""
    samples = []
    for i in range(int(total * rate)):
        t = i / rate
        samples.append(0.5 * math.sin(2 * math.pi * 1000 * t) * math.exp(-(t - lead) * 30) if t >= lead else 0.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(v * 32767)) for v in samples))
    return path


class Resp:
    def __init__(self, code=200, body=None, content=b""):
        self.status_code, self._body, self.content = code, body, content

    def json(self):
        return self._body


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.delenv(sounds.SWITCH, raising=False)
    (tmp_path / ".env").write_text("ELEVENLABS_API_KEY=sk_secret123\n")
    return tmp_path


def fake_http(monkeypatch, tier="starter", get_code=200, post_code=200, audio=None):
    seen = {"get": [], "post": []}
    mp3 = audio or b"audio"

    def get(url, headers=None, timeout=None):
        seen["get"].append(url)
        return Resp(get_code, {"tier": tier})

    def post(url, params=None, json=None, headers=None, timeout=None):
        seen["post"].append((url, params, json))
        return Resp(post_code, content=mp3)

    monkeypatch.setattr(sounds.requests, "get", get)
    monkeypatch.setattr(sounds.requests, "post", post)
    return seen


def test_free_tier_makes_no_sounds_and_says_why(home, monkeypatch):
    seen = fake_http(monkeypatch, tier="free")
    got = sounds.make()
    assert got["made"] is False and "free plan" in got["reason"] and "1(c)(i)" in got["reason"]
    assert seen["post"] == [] and not (home / "sounds" / "manifest.json").exists()
    manifest, reason = sounds.available()
    assert manifest is None and reason == got["reason"] and "free plan" in reason  # every render says why, not "run doctor"


@pytest.mark.parametrize("code", [401, 402, 403])
def test_a_refused_key_or_plan_makes_no_sounds(home, monkeypatch, code):
    seen = fake_http(monkeypatch, get_code=code)
    got = sounds.make()
    assert got["made"] is False and str(code) in got["reason"] and seen["post"] == []


def test_no_key_makes_no_sounds(home, monkeypatch):
    (home / ".env").write_text("")
    seen = fake_http(monkeypatch)
    got = sounds.make()
    assert got["made"] is False and "ELEVENLABS_API_KEY" in got["reason"] and seen == {"get": [], "post": []}


def test_no_sounds_env_switch(home, monkeypatch):
    seen = fake_http(monkeypatch)
    monkeypatch.setenv(sounds.SWITCH, "1")
    got = sounds.make()
    assert got == {"made": False, "reason": "ADEDITOR_NO_SOUNDS=1 is set"}
    assert seen == {"get": [], "post": []}
    assert sounds.available() == (None, "ADEDITOR_NO_SOUNDS=1 is set")


def test_sound_is_trimmed_to_its_transient_and_measured(tmp_path):
    src = click(tmp_path / "click.wav", lead=0.12)
    row = sounds.trim(src, tmp_path / "out.wav")
    assert row["file"] == "out.wav"
    assert row["transient"] == pytest.approx(0.005, abs=0.011)
    # 0.5 s minus about 0.115 s of leading silence
    r = sounds._decode(tmp_path / "out.wav")
    assert len(r) / 48000 == pytest.approx(0.385, abs=0.02)
    # an exponential decay at 0.5 peak: its loudest 100 ms RMS sits under the peak, well above silence
    assert -20 < row["peak100_db"] < -8
    assert abs(r[-1]) < 1e-3  # faded out


def test_make_writes_five_sounds_once_and_never_regenerates(home, monkeypatch):
    src = click(home / "src.wav")
    seen = fake_http(monkeypatch, audio=src.read_bytes())
    got = sounds.make()
    assert got == {"made": True, "reason": None}
    assert len(seen["get"]) == 1 and len(seen["post"]) == 5
    url, params, body = seen["post"][0]
    assert url == sounds.API and params == {"output_format": "mp3_44100_128"}
    assert body["model_id"] == "eleven_text_to_sound_v3" and body["duration_seconds"] >= 0.5 and body["loop"] is False
    manifest = json.loads((home / "sounds" / "manifest.json").read_text())
    assert set(manifest) == set(sounds.ROLES)
    assert set(manifest["cut"]) == {"file", "transient", "peak100_db", "made"}
    assert sounds.available()[0] == manifest and sounds.available()[1] is None
    again = sounds.make()
    assert again == {"made": False, "reason": None} and len(seen["post"]) == 5
    sounds.make(force=True)
    assert len(seen["post"]) == 10
    assert "sk_secret123" not in (home / "sounds" / "manifest.json").read_text()


def test_a_stop_part_way_keeps_what_was_made(home, monkeypatch):
    src = click(home / "src.wav")
    audio = src.read_bytes()
    calls = []

    def post(url, params=None, json=None, headers=None, timeout=None):
        calls.append(json["text"])
        return Resp(200, content=audio) if len(calls) <= 2 else Resp(402)

    monkeypatch.setattr(sounds.requests, "get", lambda *a, **k: Resp(200, {"tier": "creator"}))
    monkeypatch.setattr(sounds.requests, "post", post)
    got = sounds.make()
    assert got["made"] is True and "402" in got["reason"]
    assert sounds.available()[0] is None and "402" in sounds.available()[1]
    calls.clear()
    sounds.make()
    assert len(calls) == 3  # only the three missing ones


def test_doctor_sounds_line(home, monkeypatch):
    monkeypatch.setattr(doctor, "FACE_MODEL_PATH", home / "m.tflite")
    c = doctor.check_sounds()
    assert c.ok and c.warn and c.name == "sound effects" and "doctor --fix" in c.detail
    src = click(home / "src.wav")
    fake_http(monkeypatch, audio=src.read_bytes())
    fixed = doctor.check_sounds(fix=True)
    assert fixed.ok and not fixed.warn and "5 sounds" in fixed.detail
    monkeypatch.setenv(sounds.SWITCH, "1")
    off = doctor.check_sounds()
    assert off.ok and off.warn and "ADEDITOR_NO_SOUNDS=1" in off.detail
    assert doctor.check_sounds in doctor.CHECKS


def test_a_stop_is_remembered_until_the_sounds_are_made(home, monkeypatch):
    fake_http(monkeypatch, tier="free")
    sounds.make()
    assert "free plan" in sounds.available()[1]
    src = click(home / "src.wav")
    fake_http(monkeypatch, audio=src.read_bytes())
    assert sounds.make()["made"] is True
    assert not (home / "sounds" / sounds.LAST_REASON).exists() and sounds.available()[1] is None


def test_a_manifest_entry_without_a_file_gives_a_reason(home):
    (home / "sounds").mkdir()
    (home / "sounds" / "manifest.json").write_text(json.dumps({r: {"transient": 0} for r in sounds.ROLES}))
    manifest, reason = sounds.available()
    assert manifest is None and "0 of the 5" not in reason and "doctor --fix" in reason
    (home / "sounds" / "manifest.json").write_text(json.dumps({"cut": "oops"}))
    assert sounds.available()[0] is None and sounds.signature() == "none"


def test_the_switch_in_env_alone_turns_sounds_off(home, monkeypatch):
    (home / ".env").write_text("ELEVENLABS_API_KEY=sk_secret123\nADEDITOR_NO_SOUNDS=1\n")
    assert sounds.switched_off()
    assert sounds.available() == (None, "ADEDITOR_NO_SOUNDS=1 is set")
    import os
    assert sounds.SWITCH not in os.environ  # .env is read and never put in the environment


def test_the_environment_overrides_the_switch_in_env(home, monkeypatch):
    (home / ".env").write_text("ADEDITOR_NO_SOUNDS=1\n")
    monkeypatch.setenv(sounds.SWITCH, "0")
    assert not sounds.switched_off()
    monkeypatch.setenv(sounds.SWITCH, "1")
    (home / ".env").write_text("ADEDITOR_NO_SOUNDS=0\n")
    assert sounds.switched_off()


def test_the_doctor_line_with_the_switch_on_has_no_fix(home, monkeypatch):
    (home / ".env").write_text("ADEDITOR_NO_SOUNDS=1\n")
    c = doctor.check_sounds()
    assert c.ok and c.warn and c.fix == ""
    assert "switched off" in c.detail and "ADEDITOR_NO_SOUNDS=1" in c.detail and ".env" in c.detail
    assert "doctor --fix" not in c.detail


@pytest.mark.parametrize("code", [401, 403])
def test_a_key_that_cant_read_the_plan_or_make_sounds_says_so(home, monkeypatch, code):
    fake_http(monkeypatch, get_code=code)
    got = sounds.make()
    assert "isn't allowed to read the account's plan or make sound effects" in got["reason"]
    assert "Sound Effects and User (read)" in got["reason"] and "refused the key" not in got["reason"]
    # the same words when the plan call passes and the sound call is the one refused
    fake_http(monkeypatch, post_code=code)
    (home / "sounds" / "last-reason.txt").unlink(missing_ok=True)
    assert "isn't allowed to read the account's plan or make sound effects" in sounds.make()["reason"]


def test_a_stored_no_key_reason_is_ignored_once_the_key_is_there(home, monkeypatch):
    (home / ".env").write_text("")
    assert "ELEVENLABS_API_KEY" in sounds.make()["reason"]
    assert "ELEVENLABS_API_KEY" in sounds.available()[1]
    (home / ".env").write_text("ELEVENLABS_API_KEY=sk_secret123\n")
    assert sounds.available() == (None, "the sounds haven't been made yet; run adeditor doctor --fix")


def test_remaking_one_sound_makes_only_the_missing_one(home, monkeypatch):
    seen = fake_http(monkeypatch, audio=click(home / "src.wav").read_bytes())
    sounds.make()
    (home / "sounds" / "tick.wav").unlink()
    assert sounds.available()[0] is None
    got = sounds.make()
    assert got == {"made": True, "reason": None} and len(seen["post"]) == 6
    assert seen["post"][-1][2]["text"] == sounds.PROMPTS["tick"]["prompt"]
    assert sounds.available()[1] is None


def test_the_doctor_leaves_the_fix_out_on_a_free_plan(home, monkeypatch):
    fake_http(monkeypatch, tier="free")
    sounds.make()  # stores the free plan reason, as a run of the fix does
    check = doctor.check_sounds()
    assert check.ok and check.warn and "free plan" in check.detail and check.fix == ""
    fake_http(monkeypatch, tier="starter")  # nothing made yet: the fix is there to run
    sounds._remember(None)
    assert doctor.check_sounds().fix == "Run: adeditor doctor --fix"


def test_the_doctor_says_refused_only_when_the_key_fails_everywhere(home, monkeypatch):
    fake_http(monkeypatch, get_code=403)
    monkeypatch.setattr(doctor, "check_elevenlabs_accepts", lambda fix=False: doctor.Check("elevenlabs access", True, "accepted"))
    sounds.make()
    assert "isn't allowed to read the account's plan" in doctor.check_sounds().detail
    monkeypatch.setattr(doctor, "check_elevenlabs_accepts", lambda fix=False: doctor.Check("elevenlabs access", False, "rejected"))
    assert "ElevenLabs refused the key" in doctor.check_sounds().detail
