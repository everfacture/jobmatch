"""Detect a local Codex/ChatGPT CLI login without reading secrets.

Codex login is not an official third-party API. JobMatch can see that Codex
is installed or that a login file exists, then still score through a normal
OpenAI-compatible API key (DeepSeek, OpenAI, OpenRouter, Groq).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class CodexDetection:
    cli_path: str | None
    auth_path: Path
    auth_present: bool

    @property
    def detected(self) -> bool:
        return bool(self.cli_path or self.auth_present)

    def status_label(self) -> str:
        if self.cli_path and self.auth_present:
            return "CLI + login file"
        if self.cli_path:
            return "CLI on PATH"
        if self.auth_present:
            return "login file"
        return "not found"


def _codex_home(home: Path) -> Path:
    raw = os.environ.get("CODEX_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return home / ".codex"


def detect_local_codex(
    *,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> CodexDetection:
    """Return whether Codex appears to be installed/logged in on this machine.

    Does not open or parse auth.json. Presence of the file is enough.
    """
    home_path = home if home is not None else Path.home()
    auth_path = _codex_home(home_path) / "auth.json"
    return CodexDetection(
        cli_path=which("codex"),
        auth_path=auth_path,
        auth_present=auth_path.is_file(),
    )
