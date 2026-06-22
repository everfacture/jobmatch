"""Feature tier detection and gating."""

from rich.console import Console

from jobmatch.config.profiles import load_env

# ---------------------------------------------------------------------------
# Tier system — feature gating by installed dependencies
# ---------------------------------------------------------------------------

TIER_LABELS = {
    1: "Discovery",
    2: "AI Scoring & Tailoring",
    3: "Manual Apply Helpers",
}

TIER_COMMANDS: dict[int, list[str]] = {
    1: ["init", "run discover", "run enrich", "status", "dashboard"],
    2: ["run score", "run tailor", "run cover", "run"],
    3: ["apply", "run apply"],
}


def _has_llm_config() -> bool:
    """Return True when any supported LLM provider config is present."""
    from jobmatch.llm.client import configured_provider_labels

    try:
        return bool(configured_provider_labels())
    except RuntimeError:
        return False


def get_tier() -> int:
    """Detect the current tier based on available dependencies.

    Tier 1 (Discovery):            Python + pip
    Tier 2 (AI Scoring & Tailoring): + LLM provider config
    """
    load_env()

    if not _has_llm_config():
        return 1

    return 2


def check_tier(required: int, feature: str) -> None:
    """Raise SystemExit with a clear message if the current tier is too low.

    Args:
        required: Minimum tier needed (1 or 2).
        feature: Human-readable description of the feature being gated.
    """
    current = get_tier()
    if current >= required:
        return

    _console = Console(stderr=True)

    missing: list[str] = []
    if required >= 2 and not _has_llm_config():
        missing.append(
            "LLM provider — run [bold]jobmatch init[/bold] or set "
            "JOBMATCH_LLM_BASE_URL + JOBMATCH_LLM_API_KEY"
        )

    _console.print(
        f"\n[red]'{feature}' requires {TIER_LABELS.get(required, f'Tier {required}')} (Tier {required}).[/red]\n"
        f"Current tier: {TIER_LABELS.get(current, f'Tier {current}')} (Tier {current})."
    )
    if missing:
        _console.print("\n[yellow]Missing:[/yellow]")
        for m in missing:
            _console.print(f"  - {m}")
    _console.print()
    raise SystemExit(1)
