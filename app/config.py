"""Configuration, read from the environment with a `.env` fallback.

Nothing here is required. Every setting has a default that lets the app boot,
because the interesting failure modes are the ones you hit at runtime, not the
ones that stop you starting up.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env reader. Real values in the environment always win."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


load_dotenv()

# Read through functions, not module constants: the tests point DATABASE_URL
# at a temp file after this module has already been imported.

DEFAULT_DB = ROOT / "data" / "binrun.db"


def database_url() -> str:
    """Where state lives.

    Unset            -> a local SQLite file. Zero-config clean clone.
    postgresql://... -> hosted Postgres (Neon on the deployed instance,
                        because the Space's own filesystem is wiped on
                        restart and must never hold state).
    """
    return os.environ.get("DATABASE_URL") or f"sqlite:///{DEFAULT_DB}"


def secret_key() -> str:
    return os.environ.get("SECRET_KEY", "dev-only-secret-change-me")


def anthropic_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or None


def anthropic_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")


def nominatim_user_agent() -> str:
    return os.environ.get(
        "NOMINATIM_USER_AGENT", "binrun/0.1 (build-week project; contact unset)"
    )


def nominatim_url() -> str:
    return os.environ.get(
        "NOMINATIM_URL", "https://nominatim.openstreetmap.org/search"
    )


def geocode_timeout() -> float:
    return float(os.environ.get("GEOCODE_TIMEOUT", "6"))


def geocode_enabled() -> bool:
    return os.environ.get("GEOCODE_ENABLED", "1") not in ("0", "false", "no")


def ollama_enabled() -> bool:
    return os.environ.get("OLLAMA_ENABLED", "1") not in ("0", "false", "no")


def ollama_url() -> str:
    return os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


def ollama_model() -> str:
    return os.environ.get("OLLAMA_MODEL", "llama3.2")


# Cape Town city bowl. Only used to centre an empty map.
DEFAULT_CENTER = (-33.9249, 18.4241)
