"""Scoring preference loading.

User-specific scoring preferences live in ~/.jobmatch/preferences.yaml. The repo
ships preferences.example.yaml only; missing preferences are valid and mean the
LLM should rely on the resume/profile without deterministic target/reject rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from jobmatch.config.paths import PREFERENCES_PATH


def load_preferences(path: Path | None = None) -> dict[str, Any]:
    """Load user scoring preferences from YAML.

    Missing file returns an empty dict. That keeps existing private installs
    working while public users opt into preferences by copying the example file.
    """
    pref_path = path or PREFERENCES_PATH
    if not pref_path.exists():
        return {}
    data = yaml.safe_load(pref_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def scoring_preferences(preferences: dict[str, Any] | None) -> dict[str, Any]:
    """Return the nested scoring preferences dict, or {}."""
    if not isinstance(preferences, dict):
        return {}
    scoring = preferences.get("scoring")
    return scoring if isinstance(scoring, dict) else preferences
