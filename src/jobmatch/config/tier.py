"""Feature tier detection and gating."""

from rich.console import Console

from jobmatch.config.profiles import load_env

TIER_LABELS = {
    1: "Discovery",
    2: "AI Scoring",
}

TIER_COMMANDS: dict[int, list[str]] = {
    1: ["init", "run discover", "run enrich", "status", "dashboard"],
    2: ["run score", "run"],
}


def _has_llm_config() -> bool:
    """Return True when any supported LLM provider config is present."""
    from jobmatch.llm.client import configured_provider_labels

    try:
        return bool(configured_provider_labels())
    except RuntimeError:
        return False


def get_tier() -> int:
    """Tier 1 = Python only (discovery). Tier 2 = + an LLM provider key (scoring)."""
    load_env()
    return 2 if _has_llm_config() else 1


def check_tier(required: int, feature: str) -> None:
    """Raise SystemExit with a clear message if the current tier is too low."""
    current = get_tier()
    if current >= required:
        return

    _console = Console(stderr=True)
    _console.print(
        f"\n[red]'{feature}' requires {TIER_LABELS.get(required, f'Tier {required}')} (Tier {required}).[/red]\n"
        f"Current tier: {TIER_LABELS.get(current, f'Tier {current}')} (Tier {current})."
    )
    if required >= 2 and not _has_llm_config():
        _console.print(
            "\n[yellow]Missing:[/yellow]\n  - LLM provider — run [bold]jobmatch init[/bold] and paste a "
            "DeepSeek, OpenAI, OpenRouter, or Groq key"
        )
    _console.print()
    raise SystemExit(1)
