import pytest

from adeditor import envelope, media


def late_audio(wav, out, offset=0.5, seconds=4):
    """An mp4 whose audio stream starts `offset` s after its picture, as some cameras and phones write."""
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=30:duration={seconds}",
               "-itsoffset", str(offset), "-i", str(wav), "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)])
    return out


def test_probe_and_audio(speech, video, tmp_path):
    wav = speech([("w", 1.0), ("s", 1.0)])
    clip = video(2, 320, 240, wav)
    p = media.probe(clip)
    assert (p.width, p.height) == (320, 240)
    assert p.has_audio and abs(p.duration - 2) < 0.2 and round(p.fps) == 30 and p.rotation == 0
    out = media.extract_audio(clip, tmp_path / "a.wav")
    assert out.exists() and media.probe(clip).has_audio
    info = media.run(["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate,channels,codec_name", "-of", "csv=p=0", str(out)]).stdout.strip()
    assert info == "pcm_f32le,16000,1"


def test_probe_silent_video(video):
    assert not media.probe(video(1, 320, 240)).has_audio


def test_frames(video, tmp_path):
    assert len(media.frames(video(2, 320, 240), tmp_path / "f", fps=2)) == 4


def test_run_raises_with_stderr_tail():
    with pytest.raises(media.ToolError) as exc:
        media.run(["ffmpeg", "-v", "error", "-i", "/nonexistent.mp4", "-f", "null", "-"])
    assert "nonexistent" in str(exc.value)


def test_tool_resolves_full_path_and_names_a_missing_tool(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda n: None if n == "nope" else f"C:/bin/{n}.cmd")
    assert media.tool("npx") == "C:/bin/npx.cmd"
    with pytest.raises(media.ToolError, match="nope"):
        media.tool("nope")


def test_run_passes_the_resolved_path_and_utf8_to_subprocess(monkeypatch):
    seen = {}
    monkeypatch.setattr(media.shutil, "which", lambda n: f"C:/bin/{n}.cmd")

    def fake(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return media.subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(media.subprocess, "run", fake)
    media.run(["npx", "-y", "x"])
    assert seen["args"][0] == "C:/bin/npx.cmd" and seen["args"][1:] == ["-y", "x"]
    assert seen["kw"]["encoding"] == "utf-8" and seen["kw"]["errors"] == "replace"


def test_run_decodes_utf8_tick_marks(tmp_path):
    script = tmp_path / "p.py"
    script.write_text("import sys; sys.stdout.buffer.write('  \\u2713 Chrome\\n'.encode('utf-8'))")
    out = media.run(["python3" if media.shutil.which("python3") else "python", str(script)]).stdout
    assert out.strip().startswith("\u2713")


def test_extract_audio_keeps_the_container_clock(speech, tmp_path):
    # The tone starts 0.3 s into the audio, and the audio starts 0.5 s into the file: 0.8 s on the
    # clock the picture and ffmpeg's trims use.
    src = late_audio(speech([("s", 0.3), ("w", 0.5), ("s", 1.0)]), tmp_path / "late.mp4")
    env = envelope.envelope_db(media.extract_audio(src, tmp_path / "a.wav"))
    _, line = envelope.floor_and_line(env)
    assert abs(next(i for i, v in enumerate(env) if v > line) / 100 - 0.8) <= 0.03
