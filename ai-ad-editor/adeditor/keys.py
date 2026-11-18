"""API keys, read from ai-ad-editor/.env by code and never echoed."""
from dotenv import dotenv_values

from adeditor import paths

ELEVENLABS = "ELEVENLABS_API_KEY"
PIXABAY = "PIXABAY_API_KEY"
PEXELS = "PEXELS_API_KEY"


class MissingKey(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Missing {name}. Add {name}=your-key to ai-ad-editor/.env (copy .env.example if you have no .env yet).")


def get(name: str) -> str:
    # dotenv_values reads the file only; nothing is put in os.environ,
    # so child processes never inherit a key.
    value = dotenv_values(paths.ROOT / ".env").get(name)
    if not value or not value.strip():
        raise MissingKey(name)
    return value.strip()


def present(name: str) -> bool:
    """True when the key is set in .env (its value is never returned)."""
    try:
        get(name)
    except MissingKey:
        return False
    return True
