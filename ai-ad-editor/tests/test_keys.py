import pytest

from adeditor import keys, paths


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    return tmp_path / ".env"


def test_missing_key_names_it_and_never_shows_a_value(env):
    env.write_text("PEXELS_API_KEY=\nELEVENLABS_API_KEY=sk_secret123\n")
    with pytest.raises(keys.MissingKey) as exc:
        keys.get(keys.PEXELS)
    text = str(exc.value)
    assert "PEXELS_API_KEY" in text and "ai-ad-editor/.env" in text
    assert "sk_secret123" not in text


def test_key_is_returned_and_not_exported_to_children(env, monkeypatch):
    import os

    env.write_text("ELEVENLABS_API_KEY=sk_secret123\n")
    assert keys.get(keys.ELEVENLABS) == "sk_secret123"
    assert "ELEVENLABS_API_KEY" not in os.environ


def test_no_env_file_is_a_missing_key(env):
    with pytest.raises(keys.MissingKey):
        keys.get(keys.ELEVENLABS)
