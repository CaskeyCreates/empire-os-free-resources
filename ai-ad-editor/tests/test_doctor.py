import subprocess

import pytest
import requests

from adeditor import cli, doctor, keys, paths

FILTER_LINES = "\n".join(f" .. {f:<18} A->A  fake" for f in doctor.FILTERS)
HF_DOCTOR = "hyperframes doctor\n\n  ✓ Node.js   v24\n  ✓ Chrome    cache: /x\n  ✗ Docker    Not found\n"


class FakeResp:
    def __init__(self, code=200):
        self.status_code = code


@pytest.fixture
def healthy(tmp_path, monkeypatch):
    """Everything present, all subprocesses and network faked."""
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    model = tmp_path / "models" / "blaze_face_short_range.tflite"
    model.parent.mkdir()
    model.write_bytes(b"x" * 200_000)
    monkeypatch.setattr(doctor, "FACE_MODEL_PATH", model)
    (tmp_path / ".env").write_text("ELEVENLABS_API_KEY=sk_secret123\nPEXELS_API_KEY=px_secret456\n")
    state = {"filters": FILTER_LINES, "which_missing": set(), "hf_doctor": HF_DOCTOR, "status": 200}

    monkeypatch.setattr(doctor.shutil, "which", lambda n: None if n in state["which_missing"] else f"/bin/{n}")

    def fake_run(args, timeout=180):
        if args[0] == "ffmpeg":
            return subprocess.CompletedProcess(args, 0, state["filters"], "")
        if args[0] == "node":
            return subprocess.CompletedProcess(args, 0, "v24.15.0\n", "")
        if "doctor" in args:
            return subprocess.CompletedProcess(args, 0, state["hf_doctor"], "")
        return subprocess.CompletedProcess(args, 0, "0.7.5\n", "")

    monkeypatch.setattr(doctor, "_run", fake_run)
    def fake_get(url, headers=None, timeout=None, params=None):
        state.setdefault("asked", []).append((url, params))
        return FakeResp(state["status"])

    monkeypatch.setattr(doctor.requests, "get", fake_get)
    return state


def by_name(results):
    return {c.name: c for c in results}


def test_doctor_passes_when_everything_is_present(healthy):
    assert all(c.ok for c in doctor.run())


def test_doctor_reports_a_missing_tool(healthy):
    healthy["which_missing"] = {"ffprobe"}
    c = by_name(doctor.run())["ffprobe"]
    assert not c.ok
    assert "brew install ffmpeg" in c.fix and "winget install Gyan.FFmpeg" in c.fix


def test_doctor_checks_the_filters_the_mix_needs(healthy):
    healthy["filters"] = "\n".join(l for l in FILTER_LINES.splitlines() if "sidechaincompress" not in l)
    c = by_name(doctor.run())["ffmpeg"]
    assert not c.ok and "sidechaincompress" in c.detail


def test_doctor_fails_when_hyperframes_has_no_browser(healthy):
    healthy["hf_doctor"] = "  ✗ Chrome    Not found\n"
    assert not by_name(doctor.run())["hyperframes browser"].ok


def test_doctor_names_a_missing_elevenlabs_key_and_skips_its_network_check(healthy, tmp_path):
    (tmp_path / ".env").write_text("PEXELS_API_KEY=px_secret456\n")
    r = by_name(doctor.run())
    assert not r["elevenlabs key"].ok and "ELEVENLABS_API_KEY" in r["elevenlabs key"].fix
    assert not r["elevenlabs access"].ok and r["b-roll key"].ok


def test_broll_key_passes_with_an_accepted_pixabay_key_and_searches_office(healthy, tmp_path):
    (tmp_path / ".env").write_text("ELEVENLABS_API_KEY=sk_secret123\nPIXABAY_API_KEY=pbx_secret789\n")
    c = by_name(doctor.run())["b-roll key"]
    assert c.ok and not c.warn and "pixabay" in c.detail
    assert ("https://pixabay.com/api/videos/", {"key": "pbx_secret789", "q": "office", "per_page": 3}) in healthy["asked"]


def test_broll_key_passes_with_an_accepted_pexels_key(healthy):
    c = by_name(doctor.run())["b-roll key"]
    assert c.ok and "pexels" in c.detail


def test_broll_key_fails_when_the_key_is_rejected_without_showing_it(healthy, tmp_path):
    (tmp_path / ".env").write_text("ELEVENLABS_API_KEY=sk_secret123\nPIXABAY_API_KEY=pbx_secret789\n")
    healthy["status"] = 400
    c = by_name(doctor.run())["b-roll key"]
    assert not c.ok and "400" in c.detail and "PIXABAY_API_KEY" in c.fix
    assert "pbx_secret789" not in doctor.format_table(doctor.run())


def test_broll_key_warns_naming_pixabay_when_only_pexels_works(healthy, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ELEVENLABS_API_KEY=sk_secret123\nPIXABAY_API_KEY=pbx_secret789\nPEXELS_API_KEY=px_secret456\n")
    monkeypatch.setattr(doctor.requests, "get", lambda url, headers=None, timeout=None, params=None: FakeResp(400 if "pixabay" in url else 200))
    c = by_name(doctor.run())["b-roll key"]
    assert c.ok and c.warn and "pixabay" in c.detail and "PIXABAY_API_KEY" in c.fix
    assert "pbx_secret789" not in doctor.format_table(doctor.run())


def test_a_pixabay_429_says_wait_a_minute_not_check_the_key(healthy, tmp_path):
    (tmp_path / ".env").write_text("ELEVENLABS_API_KEY=sk_secret123\nPIXABAY_API_KEY=pbx_secret789\n")
    healthy["status"] = 429
    c = by_name(doctor.run())["b-roll key"]
    assert not c.ok and "429" in c.detail and "Wait a minute" in c.fix and "full key" not in c.fix


def test_broll_key_only_warns_with_no_stock_key(healthy, tmp_path):
    (tmp_path / ".env").write_text("ELEVENLABS_API_KEY=sk_secret123\n")
    results = doctor.run()
    c = by_name(results)["b-roll key"]
    assert c.ok and c.warn and "own clips" in c.detail and "PIXABAY_API_KEY" in c.fix
    table = doctor.format_table(results)
    assert "warn  b-roll key" in table and "Fix: For stock B-roll" in table
    assert all(r.ok for r in results)


def test_doctor_reports_a_rejected_key_without_the_value(healthy):
    healthy["status"] = 401
    c = by_name(doctor.run())["elevenlabs access"]
    assert not c.ok and "401" in c.detail
    assert "sk_secret123" not in doctor.format_table(doctor.run())


def test_doctor_survives_a_network_error(healthy, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down sk_secret123")

    monkeypatch.setattr(doctor.requests, "get", boom)
    c = by_name(doctor.run())["elevenlabs access"]
    assert not c.ok and "sk_secret123" not in c.detail


def test_cli_doctor_exit_code(healthy, capsys):
    assert cli.main(["doctor"]) == 0
    healthy["which_missing"] = {"ffprobe"}
    assert cli.main(["doctor"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_doctor_hands_subprocess_the_resolved_npx_path_and_reads_utf8(monkeypatch):
    seen = []
    monkeypatch.setattr(doctor.media.shutil, "which", lambda n: f"C:/bin/{n}.cmd")

    def fake(args, **kw):
        seen.append((args, kw))
        return subprocess.CompletedProcess(args, 0, HF_DOCTOR, "")

    monkeypatch.setattr(doctor.subprocess, "run", fake)
    c = doctor.check_hyperframes_browser()
    assert c.ok
    assert seen[0][0][0] == "C:/bin/npx.cmd"
    assert seen[0][1]["encoding"] == "utf-8"


def test_doctor_decodes_utf8_tick_marks_through_the_real_decode_path(monkeypatch):
    real = subprocess.run

    def via_python(args, **kw):
        code = "import sys; sys.stdout.buffer.write('  \\u2713 Chrome    cache\\n'.encode('utf-8'))"
        return real([sys.executable, "-c", code], **kw)

    import sys

    monkeypatch.setattr(doctor.media.shutil, "which", lambda n: f"/bin/{n}")
    monkeypatch.setattr(doctor.subprocess, "run", via_python)
    assert doctor.check_hyperframes_browser().ok


def test_doctor_reports_missing_npx_without_crashing(monkeypatch):
    monkeypatch.setattr(doctor.media.shutil, "which", lambda n: None)
    c = doctor.check_hyperframes()
    assert not c.ok and "npx" in c.detail


class Download:
    def __init__(self, content=b"m" * 229_746, code=200):
        self.content, self.code = content, code

    def raise_for_status(self):
        if self.code >= 400:
            raise requests.HTTPError(f"HTTP {self.code}")


def serve(download, seen=None):
    """requests.get: the model download gets `download`; the key checks still get a 200."""
    def get(url, headers=None, timeout=None):
        if headers is not None:
            return FakeResp(200)
        if seen is not None:
            seen.append(url)
        return download
    return get


def test_face_model_url_is_googles_mediapipe_storage():
    assert doctor.FACE_MODEL_URL == ("https://storage.googleapis.com/mediapipe-models/face_detector/"
                                     "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite")
    assert doctor.FACE_MODEL_PATH.name == "blaze_face_short_range.tflite"
    assert doctor.FACE_MODEL_PATH.parent.name == "models"


def test_doctor_names_a_missing_face_model_and_fix_downloads_it(healthy, monkeypatch):
    doctor.FACE_MODEL_PATH.unlink()
    c = by_name(doctor.run())["face model"]
    assert not c.ok and "doctor --fix" in c.fix
    seen = []
    monkeypatch.setattr(doctor.requests, "get", serve(Download(), seen))
    c = by_name(doctor.run(fix=True))["face model"]
    assert c.ok and seen == [doctor.FACE_MODEL_URL]
    assert doctor.FACE_MODEL_PATH.stat().st_size == 229_746
    assert [p.name for p in doctor.FACE_MODEL_PATH.parent.iterdir()] == ["blaze_face_short_range.tflite"]


def test_a_tiny_download_is_refused_and_leaves_nothing_behind(healthy, monkeypatch):
    doctor.FACE_MODEL_PATH.unlink()
    monkeypatch.setattr(doctor.requests, "get", serve(Download(b"<html>no</html>")))
    c = by_name(doctor.run(fix=True))["face model"]
    assert not c.ok and "too small" in c.detail
    assert list(doctor.FACE_MODEL_PATH.parent.iterdir()) == []


def test_a_failed_model_download_says_so(healthy, monkeypatch):
    doctor.FACE_MODEL_PATH.unlink()
    monkeypatch.setattr(doctor.requests, "get", serve(Download(code=503)))
    c = by_name(doctor.run(fix=True))["face model"]
    assert not c.ok and "download failed" in c.detail
    assert list(doctor.FACE_MODEL_PATH.parent.iterdir()) == []
