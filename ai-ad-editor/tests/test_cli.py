"""Whatever goes wrong, a person sees one plain line, never a traceback (unless they ask for one)."""
import pytest

from adeditor import cli


@pytest.fixture
def boom(monkeypatch):
    """A command that fails the way a bug would."""
    cli.build_parser()  # the real commands first, so the fake one is added beside them

    def fail(ns):
        raise KeyError("from")

    monkeypatch.setitem(cli.COMMANDS, "boom", {"fn": fail, "help": "fails", "args": None})


def test_an_unexpected_error_is_one_plain_line(boom, monkeypatch, capsys):
    monkeypatch.delenv("ADEDITOR_DEBUG", raising=False)
    assert cli.main(["boom"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert len(err.strip().splitlines()) == 1
    assert "KeyError" in err and "ADEDITOR_DEBUG=1" in err


def test_adeditor_debug_shows_the_traceback(boom, monkeypatch, capsys):
    monkeypatch.setenv("ADEDITOR_DEBUG", "1")
    assert cli.main(["boom"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" in err and "KeyError" in err


def test_a_plain_value_error_reads_as_it_was_written(monkeypatch, capsys):
    monkeypatch.delenv("ADEDITOR_DEBUG", raising=False)
    assert cli.main(["plan", "check", "Bad Slug", "take1.mp4"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err and len(err.strip().splitlines()) == 1
    assert "lower-case letters, digits and hyphens" in err


def test_every_command_finds_a_bare_video_name_in_raw_or_says_what_is_there(tmp_path, monkeypatch, capsys):
    from adeditor import paths, transcribe

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.delenv("ADEDITOR_DEBUG", raising=False)
    raw = tmp_path / "clients" / "acme" / "raw"
    raw.mkdir(parents=True)
    (raw / "take-landscape.mov").write_bytes(b"x")
    seen = []
    monkeypatch.setattr(transcribe, "prepare", lambda job, keyterms: seen.append(job.video) or [])
    assert cli.main(["prepare", "acme", "take-landscape.mov"]) == 0
    assert seen == [raw / "take-landscape.mov"]
    capsys.readouterr()
    for argv in (["prepare", "acme", "take.mov"], ["cut", "acme", "take.mov"], ["approve-cut", "acme", "take.mov"],
                 ["accept-words", "acme", "take.mov"], ["speed", "acme", "take.mov", "1.25"],
                 ["plan", "check", "acme", "take.mov"], ["broll", "acme", "take.mov", "desk"],
                 ["render", "acme", "take.mov"], ["check", "acme", "take.mov"]):
        assert cli.main(argv) == 1, argv
        err = capsys.readouterr().err
        assert "Traceback" not in err and len(err.strip().splitlines()) == 1, (argv, err)
        assert "take.mov" in err and "take-landscape.mov" in err and "ffprobe" not in err, (argv, err)


def test_a_deleted_raw_take_still_renders_from_its_work_folder(tmp_path, monkeypatch, capsys):
    from adeditor import paths

    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.delenv("ADEDITOR_DEBUG", raising=False)
    (tmp_path / "clients" / "acme" / "raw").mkdir(parents=True)
    (tmp_path / "clients" / "acme" / "work" / "take-landscape").mkdir(parents=True)
    # the commands that only read work/<stem>/ go on from the bare stem or name
    for name in ("take-landscape.mov", "take-landscape"):
        job = paths.job("acme", name)
        assert job.work == tmp_path / "clients" / "acme" / "work" / "take-landscape"
    for argv in (["approve-cut", "acme", "take-landscape.mov"], ["accept-words", "acme", "take-landscape.mov"],
                 ["speed", "acme", "take-landscape.mov", "1.25"], ["plan", "check", "acme", "take-landscape.mov"],
                 ["broll", "acme", "take-landscape.mov", "--get", "x"],
                 ["render", "acme", "take-landscape.mov"], ["check", "acme", "take-landscape.mov"]):
        cli.main(argv)
        assert "We can't find the video" not in capsys.readouterr().err, argv
    # prepare and cut read the raw take itself, so they still need it
    for argv in (["prepare", "acme", "take-landscape.mov"], ["cut", "acme", "take-landscape.mov"]):
        assert cli.main(argv) == 1
        assert "We can't find the video take-landscape.mov" in capsys.readouterr().err, argv
    with pytest.raises(paths.VideoNotFound):
        paths.job("acme", "take-landscape.mov", need_raw=True)
