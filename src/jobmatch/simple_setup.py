"""Non-interactive civilian setup writers.

Used by the local app UI. Keep secrets out of logs.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from jobmatch.config import (
    ENV_PATH,
    PREFERENCES_PATH,
    PROFILE_PATH,
    RESUME_PATH,
    RESUME_PDF_PATH,
    SEARCH_CONFIG_PATH,
    ensure_dirs,
)
from jobmatch.llm.providers import SIMPLE_PROVIDERS, render_llm_env
from jobmatch.wizard.init import build_preferences_yaml, build_simple_profile


def write_search_config(location: str, roles: list[str]) -> None:
    loc = location.strip() or "Remote"
    clean_roles = [r.strip() for r in roles if r.strip()]
    if not clean_roles:
        clean_roles = ["Software Engineer"]
    remote = loc.casefold() in {"remote", "anywhere", "wfh"}
    distance = 0 if remote else 25
    lines = [
        "# JobMatch search configuration",
        "# Edit this file to refine your job search queries.",
        "",
        "defaults:",
        f'  location: "{loc}"',
        f"  distance: {distance}",
        "  hours_old: 72",
        "  results_per_site: 50",
        "",
        "boards:",
        "  - linkedin",
        "",
        "locations:",
        f'  - location: "{loc}"',
        f'    label: "{loc}"',
        f"    remote: {str(remote).lower()}",
        "",
        "queries:",
    ]
    for i, role in enumerate(clean_roles):
        lines.append(f'  - query: "{role}"')
        lines.append(f"    tier: {min(i + 1, 3)}")
    SEARCH_CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def apply_simple_setup(
    *,
    full_name: str,
    email: str,
    city: str,
    country: str,
    location: str,
    roles: list[str],
    provider: str,
    api_key: str,
    resume_text: str = "",
    resume_src: Path | None = None,
) -> None:
    """Write ~/.jobmatch runtime files for the short civilian path."""
    name = full_name.strip()
    if not name:
        raise ValueError("Name is required.")
    clean_roles = [r.strip() for r in roles if r.strip()]
    if not clean_roles:
        raise ValueError("Type at least one job title.")
    provider_key = provider.strip().lower()
    if provider_key not in SIMPLE_PROVIDERS:
        raise ValueError("Pick DeepSeek, OpenAI, OpenRouter, or Groq.")
    if not api_key.strip():
        raise ValueError("Paste an API key.")

    ensure_dirs()

    text = resume_text.strip()
    if resume_src is not None:
        src = Path(resume_src).expanduser().resolve()
        if not src.is_file():
            raise ValueError("Resume file not found.")
        suffix = src.suffix.lower()
        if suffix == ".txt":
            shutil.copy2(src, RESUME_PATH)
            text = text or RESUME_PATH.read_text(encoding="utf-8", errors="replace")
        elif suffix == ".pdf":
            shutil.copy2(src, RESUME_PDF_PATH)
        else:
            raise ValueError("Resume must be .txt or .pdf.")
    if text:
        RESUME_PATH.write_text(text, encoding="utf-8")
    if not RESUME_PATH.is_file():
        raise ValueError("Paste your CV or upload a .txt resume.")

    target_role = clean_roles[0]
    profile = build_simple_profile(
        full_name=name,
        email=email,
        city=city,
        country=country,
        target_role=target_role,
    )
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    write_search_config(location, clean_roles)
    PREFERENCES_PATH.write_text(build_preferences_yaml(profile, clean_roles), encoding="utf-8")
    preset = SIMPLE_PROVIDERS[provider_key]
    ENV_PATH.write_text(
        render_llm_env(
            base_url=preset.base_url,
            api_key=api_key.strip(),
            model=preset.model,
            notifier="console",
        ),
        encoding="utf-8",
    )


def is_configured() -> bool:
    return PROFILE_PATH.is_file() and SEARCH_CONFIG_PATH.is_file() and RESUME_PATH.is_file()
