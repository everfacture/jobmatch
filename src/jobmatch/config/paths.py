"""Path constants and directory management."""

import os
from pathlib import Path


def _base_app_dir() -> Path:
    return Path(os.environ.get("JOBMATCH_DIR", Path.home() / ".jobmatch"))


def _profile_name() -> str:
    return os.environ.get("JOBMATCH_PROFILE", "").strip()


def _app_dir() -> Path:
    """Return active JobMatch data directory.

    Default profile keeps legacy layout at ~/.jobmatch. Named profiles live under
    ~/.jobmatch/profiles/<name> so different candidates never share DB/artifacts.
    """
    base = _base_app_dir()
    profile = _profile_name()
    if not profile or profile in ("default", "legacy"):
        return base
    safe = profile.replace("/", "-").replace("..", "-")
    return base / "profiles" / safe


# User data directory — all user-specific files live here.
# Set JOBMATCH_PROFILE before importing jobmatch.config to use a named profile.
APP_DIR = _app_dir()
PROFILE_NAME = _profile_name() or "default"

# Core paths
DB_PATH = APP_DIR / "jobmatch.db"
PROFILE_PATH = APP_DIR / "profile.json"
RESUME_PATH = APP_DIR / "resume.txt"
RESUME_PDF_PATH = APP_DIR / "resume.pdf"
SEARCH_CONFIG_PATH = APP_DIR / "searches.yaml"
PREFERENCES_PATH = APP_DIR / "preferences.yaml"
ENV_PATH = APP_DIR / ".env"

LOG_DIR = APP_DIR / "logs"

# Package-shipped config (YAML registries)
PACKAGE_DIR = Path(__file__).parent.parent
CONFIG_DIR = PACKAGE_DIR / "config"


def ensure_dirs():
    """Create the runtime directories."""
    for d in (APP_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def write_private(path: Path, text: str) -> None:
    """Write a file only the current user can read (API keys, bot tokens)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)  # an existing file keeps its old mode through O_TRUNC
