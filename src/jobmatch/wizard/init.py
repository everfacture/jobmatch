"""JobMatch first-time setup wizard.

Interactive flow that creates ~/.jobmatch/ with:
  - resume.txt (and optionally resume.pdf)
  - profile.json
  - searches.yaml
  - preferences.yaml
  - .env (LLM API key)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
import yaml

from jobmatch.config import (
    APP_DIR,
    ENV_PATH,
    PREFERENCES_PATH,
    PROFILE_PATH,
    RESUME_PATH,
    RESUME_PDF_PATH,
    SEARCH_CONFIG_PATH,
    ensure_dirs,
)

console = Console()


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def _setup_resume() -> None:
    """Prompt for resume file and copy into APP_DIR."""
    console.print(Panel("[bold]Step 1: Resume[/bold]\nPoint to your master resume file (.txt or .pdf)."))

    while True:
        path_str = Prompt.ask("Resume file path")
        src = Path(path_str.strip().strip('"').strip("'")).expanduser().resolve()

        if not src.exists():
            console.print(f"[red]File not found:[/red] {src}")
            continue

        suffix = src.suffix.lower()
        if suffix not in (".txt", ".pdf"):
            console.print("[red]Unsupported format.[/red] Provide a .txt or .pdf file.")
            continue

        if suffix == ".txt":
            shutil.copy2(src, RESUME_PATH)
            console.print(f"[green]Copied to {RESUME_PATH}[/green]")
        elif suffix == ".pdf":
            shutil.copy2(src, RESUME_PDF_PATH)
            console.print(f"[green]Copied to {RESUME_PDF_PATH}[/green]")

            # Also ask for a plain-text version for LLM consumption
            txt_path_str = Prompt.ask(
                "Plain-text version of your resume (.txt)",
                default="",
            )
            if txt_path_str.strip():
                txt_src = Path(txt_path_str.strip().strip('"').strip("'")).expanduser().resolve()
                if txt_src.exists():
                    shutil.copy2(txt_src, RESUME_PATH)
                    console.print(f"[green]Copied to {RESUME_PATH}[/green]")
                else:
                    console.print("[yellow]File not found, skipping plain-text copy.[/yellow]")
        break


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def _setup_profile() -> dict:
    """Walk through profile questions and return a nested profile dict."""
    console.print(Panel("[bold]Step 2: Profile[/bold]\nTell JobMatch about yourself. This powers scoring, tailoring, and optional apply helpers."))

    profile: dict = {}

    # -- Personal --
    console.print("\n[bold cyan]Personal Information[/bold cyan]")
    full_name = Prompt.ask("Full name")
    profile["personal"] = {
        "full_name": full_name,
        "preferred_name": Prompt.ask("Preferred/nickname (leave blank to use first name)", default=""),
        "email": Prompt.ask("Email address"),
        "phone": Prompt.ask("Phone number", default=""),
        "city": Prompt.ask("City"),
        "province_state": Prompt.ask("Province/State (e.g. Ontario, California)", default=""),
        "country": Prompt.ask("Country"),
        "postal_code": Prompt.ask("Postal/ZIP code", default=""),
        "address": Prompt.ask("Street address (optional, used for form auto-fill)", default=""),
        "linkedin_url": Prompt.ask("LinkedIn URL", default=""),
        "github_url": Prompt.ask("GitHub URL (optional)", default=""),
        "portfolio_url": Prompt.ask("Portfolio URL (optional)", default=""),
        "website_url": Prompt.ask("Personal website URL (optional)", default=""),
        "password": "",
    }

    # -- Work Authorization --
    console.print("\n[bold cyan]Work Authorization[/bold cyan]")
    profile["work_authorization"] = {
        "legally_authorized_to_work": Confirm.ask("Are you legally authorized to work in your target country?"),
        "require_sponsorship": Confirm.ask("Will you now or in the future need sponsorship?"),
        "work_permit_type": Prompt.ask("Work permit type (e.g. Citizen, PR, Open Work Permit — leave blank if N/A)", default=""),
    }

    # -- Compensation --
    console.print("\n[bold cyan]Compensation[/bold cyan]")
    salary = Prompt.ask("Expected annual salary (number)", default="")
    salary_currency = Prompt.ask("Currency", default="USD")
    salary_range = Prompt.ask("Acceptable range (e.g. 80000-120000)", default="")
    range_parts = salary_range.split("-") if "-" in salary_range else [salary, salary]
    profile["compensation"] = {
        "salary_expectation": salary,
        "salary_currency": salary_currency,
        "salary_range_min": range_parts[0].strip(),
        "salary_range_max": range_parts[1].strip() if len(range_parts) > 1 else range_parts[0].strip(),
    }

    # -- Experience --
    console.print("\n[bold cyan]Experience[/bold cyan]")
    current_title = Prompt.ask("Current/most recent job title", default="")
    target_role = Prompt.ask("Target role (what you're applying for, e.g. 'Senior Backend Engineer')", default=current_title)
    profile["experience"] = {
        "years_of_experience_total": Prompt.ask("Years of professional experience", default=""),
        "education_level": Prompt.ask("Highest education (e.g. Bachelor's, Master's, PhD, Self-taught)", default=""),
        "current_title": current_title,
        "target_role": target_role,
    }

    # -- Skills Boundary --
    console.print("\n[bold cyan]Skills[/bold cyan] (comma-separated)")
    langs = Prompt.ask("Programming languages", default="")
    frameworks = Prompt.ask("Frameworks & libraries", default="")
    tools = Prompt.ask("Tools & platforms (e.g. Docker, AWS, Git)", default="")
    profile["skills_boundary"] = {
        "programming_languages": [s.strip() for s in langs.split(",") if s.strip()],
        "frameworks": [s.strip() for s in frameworks.split(",") if s.strip()],
        "tools": [s.strip() for s in tools.split(",") if s.strip()],
    }

    # -- Resume Facts (preserved truths for tailoring) --
    console.print("\n[bold cyan]Resume Facts[/bold cyan]")
    console.print("[dim]These are preserved exactly during resume tailoring — the AI will never change them.[/dim]")
    companies = Prompt.ask("Companies to always keep (comma-separated)", default="")
    projects = Prompt.ask("Projects to always keep (comma-separated)", default="")
    school = Prompt.ask("School name(s) to preserve", default="")
    metrics = Prompt.ask("Real metrics to preserve (e.g. '99.9% uptime, 50k users')", default="")
    profile["resume_facts"] = {
        "preserved_companies": [s.strip() for s in companies.split(",") if s.strip()],
        "preserved_projects": [s.strip() for s in projects.split(",") if s.strip()],
        "preserved_school": school.strip(),
        "real_metrics": [s.strip() for s in metrics.split(",") if s.strip()],
    }

    # -- EEO Voluntary (defaults) --
    profile["eeo_voluntary"] = {
        "gender": "Decline to self-identify",
        "race_ethnicity": "Decline to self-identify",
        "veteran_status": "Decline to self-identify",
        "disability_status": "Decline to self-identify",
    }

    # -- Availability --
    profile["availability"] = {
        "earliest_start_date": Prompt.ask("Earliest start date", default="Immediately"),
    }

    # Save
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"\n[green]Profile saved to {PROFILE_PATH}[/green]")
    return profile


# ---------------------------------------------------------------------------
# Search config
# ---------------------------------------------------------------------------

def _setup_searches() -> list[str]:
    """Generate a searches.yaml from user input."""
    console.print(Panel("[bold]Step 3: Job Search Config[/bold]\nDefine what you're looking for."))

    location = Prompt.ask("Target location (e.g. 'Remote', 'Canada', 'New York, NY')", default="Remote")
    distance_str = Prompt.ask("Search radius in miles (0 for remote-only)", default="0")
    try:
        distance = int(distance_str)
    except ValueError:
        distance = 0

    roles_raw = Prompt.ask(
        "Target job titles (comma-separated, e.g. 'Backend Engineer, Full Stack Developer')"
    )
    roles = [r.strip() for r in roles_raw.split(",") if r.strip()]

    if not roles:
        console.print("[yellow]No roles provided. Using a default set.[/yellow]")
        roles = ["Software Engineer"]

    # Build YAML content
    lines = [
        "# JobMatch search configuration",
        "# Edit this file to refine your job search queries.",
        "",
        "defaults:",
        f'  location: "{location}"',
        f"  distance: {distance}",
        "  hours_old: 72",
        "  results_per_site: 50",
        "",
        "# LinkedIn is the safest default. Add indeed/glassdoor/etc. later if needed.",
        "boards:",
        "  - linkedin",
        "",
        "locations:",
        f'  - location: "{location}"',
        f'    label: "{location}"',
        f"    remote: {str(distance == 0).lower()}",
        "",
        "queries:",
    ]
    for i, role in enumerate(roles):
        lines.append(f'  - query: "{role}"')
        lines.append(f"    tier: {min(i + 1, 3)}")

    SEARCH_CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"[green]Search config saved to {SEARCH_CONFIG_PATH}[/green]")
    return roles


# ---------------------------------------------------------------------------
# Scoring preferences
# ---------------------------------------------------------------------------

def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _skill_signals(profile: dict) -> list[str]:
    skills = profile.get("skills_boundary") if isinstance(profile, dict) else {}
    if not isinstance(skills, dict):
        return []
    signals: list[str] = []
    for value in skills.values():
        if isinstance(value, list):
            signals.extend(str(item).strip() for item in value if str(item).strip())
    return _unique(signals)[:12]


def build_preferences_yaml(
    profile: dict,
    roles: list[str],
    *,
    headline: str = "",
    adjacent_roles: list[str] | None = None,
    reject_roles: list[str] | None = None,
    dealbreakers: list[str] | None = None,
    positive_signals: list[str] | None = None,
    negative_signals: list[str] | None = None,
) -> str:
    """Build a starter preferences.yaml from wizard answers.

    The file is intentionally user-owned runtime config. It gives scoring enough
    deterministic guardrails that obvious yes/no jobs do not all require paid LLM
    calls, while keeping the public repo free of one candidate's private lane.
    """
    experience = profile.get("experience") if isinstance(profile, dict) else {}
    if not isinstance(experience, dict):
        experience = {}

    target_role = str(experience.get("target_role") or experience.get("current_title") or "").strip()
    target_roles = _unique(([target_role] if target_role else []) + roles)
    signals = positive_signals if positive_signals is not None else _skill_signals(profile)

    preferences = {
        "candidate": {
            "headline": headline or (f"Candidate targeting {target_roles[0]} roles" if target_roles else "Candidate"),
        },
        "scoring": {
            "min_score": 7,
            "target_roles": target_roles,
            "adjacent_roles": adjacent_roles or [],
            "reject_roles": reject_roles or [],
            "dealbreakers": dealbreakers or ["unpaid", "commission only"],
            "positive_signals": signals,
            "negative_signals": negative_signals or [],
        },
    }
    return yaml.safe_dump(preferences, sort_keys=False, allow_unicode=True)


def _setup_preferences(profile: dict, roles: list[str]) -> None:
    """Generate a starter preferences.yaml for deterministic scoring guardrails."""
    console.print(Panel(
        "[bold]Step 4: Scoring Preferences[/bold]\n"
        "These rules cut noise and reduce paid AI scoring calls. You can edit them later."
    ))

    if PREFERENCES_PATH.exists() and not Confirm.ask(
        f"Overwrite existing preferences at {PREFERENCES_PATH}?",
        default=False,
    ):
        console.print(f"[yellow]Keeping existing {PREFERENCES_PATH}[/yellow]")
        return

    experience = profile.get("experience") if isinstance(profile, dict) else {}
    target_role = ""
    if isinstance(experience, dict):
        target_role = str(experience.get("target_role") or experience.get("current_title") or "").strip()

    headline = Prompt.ask(
        "One-line candidate headline for scoring",
        default=f"Candidate targeting {target_role or (roles[0] if roles else 'selected')} roles",
    )
    adjacent = _split_csv(Prompt.ask("Adjacent/acceptable roles (comma-separated, optional)", default=""))
    rejects = _split_csv(Prompt.ask("Job titles/roles to reject (comma-separated, optional)", default=""))
    dealbreakers = _split_csv(Prompt.ask("Dealbreaker phrases", default="unpaid, commission only"))
    negatives = _split_csv(Prompt.ask("Negative signals (comma-separated, optional)", default=""))

    PREFERENCES_PATH.write_text(
        build_preferences_yaml(
            profile,
            roles,
            headline=headline,
            adjacent_roles=adjacent,
            reject_roles=rejects,
            dealbreakers=dealbreakers,
            negative_signals=negatives,
        ),
        encoding="utf-8",
    )
    console.print(f"[green]Scoring preferences saved to {PREFERENCES_PATH}[/green]")


# ---------------------------------------------------------------------------
# AI Features
# ---------------------------------------------------------------------------

def _setup_ai_features() -> None:
    """Ask about AI scoring/tailoring — optional LLM configuration."""
    console.print(Panel(
        "[bold]Step 5: AI Features (optional)[/bold]\n"
        "An LLM powers job scoring and fit analysis.\n"
        "Without this, you can still discover and enrich jobs."
    ))

    if not Confirm.ask("Enable AI scoring and resume tailoring now?", default=False):
        console.print("[dim]Discovery-only mode. You can configure AI later with [bold]jobmatch init[/bold].[/dim]")
        return

    console.print(
        "Supported providers: OpenAI-compatible endpoints — "
        "[bold]OpenAI[/bold], OpenRouter, DeepSeek, Groq, Gemini, or local (Ollama/LM Studio)."
    )
    provider = Prompt.ask(
        "Provider",
        choices=["openai", "openrouter", "deepseek", "groq", "gemini", "local"],
        default="openai",
    )

    presets = {
        "openai": ("https://api.openai.com/v1", "gpt-4o-mini", "OpenAI API key"),
        "openrouter": ("https://openrouter.ai/api/v1", "openai/gpt-4o-mini", "OpenRouter API key"),
        "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat", "DeepSeek API key"),
        "groq": ("https://api.groq.com/openai/v1", "llama-3.1-8b-instant", "Groq API key"),
        "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash", "Gemini API key"),
    }

    env_lines = [
        "# JobMatch private secrets",
        "# Generated by jobmatch init. Do not commit this file.",
        "",
    ]

    if provider == "local":
        url = Prompt.ask("OpenAI-compatible local endpoint URL", default="http://localhost:11434/v1")
        model = Prompt.ask("Model name", default="llama3.1")
        api_key = Prompt.ask("API key (leave blank if local server does not require one)", default="")
    else:
        default_url, default_model, key_prompt = presets[provider]
        url = Prompt.ask("Base URL", default=default_url)
        model = Prompt.ask("Model", default=default_model)
        api_key = Prompt.ask(key_prompt, password=True)

    env_lines.extend([
        f"JOBMATCH_LLM_BASE_URL={url.rstrip('/')}",
        f"JOBMATCH_LLM_API_KEY={api_key}",
        f"JOBMATCH_LLM_MODEL={model}",
    ])

    env_lines.append("")
    ENV_PATH.write_text("\n".join(env_lines), encoding="utf-8")
    console.print(f"[green]AI configuration saved to {ENV_PATH}[/green]")


# ---------------------------------------------------------------------------
# Manual Apply Helpers
# --------------------------------------------------------------------------

def _setup_auto_apply() -> None:
    """Configure manual apply helpers (requires Claude Code CLI)."""
    console.print(Panel(
        "[bold]Step 6: Manual Apply Helpers (optional)[/bold]\n"
        "Cover letters and apply packs only — fill and submit job applications\n"
        "using Claude Code as the browser agent."
    ))

    if not Confirm.ask("Enable manual apply helpers?", default=False):
        console.print("[dim]You can apply manually using the apply packs JobMatch generates.[/dim]")
        return

    # Check for Claude Code CLI
    if shutil.which("claude"):
        console.print("[green]Claude Code CLI detected.[/green]")
    else:
        console.print(
            "[yellow]Claude Code CLI not found on PATH.[/yellow]\n"
            "Install it from: [bold]https://claude.ai/code[/bold]\n"
            "Browser apply helpers won't work until Claude Code is installed."
        )

    # Optional: CapSolver for CAPTCHAs
    console.print("\n[dim]Some job sites use CAPTCHAs. CapSolver can handle them automatically.[/dim]")
    if Confirm.ask("Configure CapSolver API key? (optional)", default=False):
        capsolver_key = Prompt.ask("CapSolver API key")
        # Append to existing .env or create
        if ENV_PATH.exists():
            existing = ENV_PATH.read_text(encoding="utf-8")
            if "CAPSOLVER_API_KEY" not in existing:
                ENV_PATH.write_text(
                    existing.rstrip() + f"\nCAPSOLVER_API_KEY={capsolver_key}\n",
                    encoding="utf-8",
                )
        else:
            ENV_PATH.write_text(f"# JobMatch configuration\nCAPSOLVER_API_KEY={capsolver_key}\n", encoding="utf-8")
        console.print("[green]CapSolver key saved.[/green]")
    else:
        console.print("[dim]Skipped. Add CAPSOLVER_API_KEY to .env later if needed.[/dim]")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run_wizard() -> None:
    """Run the full interactive setup wizard."""
    console.print()
    console.print(
        Panel.fit(
            "[bold green]JobMatch Setup Wizard[/bold green]\n\n"
            "This will create your configuration at:\n"
            f"  [cyan]{APP_DIR}[/cyan]\n\n"
            "You can re-run this anytime with [bold]jobmatch init[/bold].",
            border_style="green",
        )
    )

    ensure_dirs()
    console.print(f"[dim]Created {APP_DIR}[/dim]\n")

    # Step 1: Resume
    _setup_resume()
    console.print()

    # Step 2: Profile
    profile = _setup_profile()
    console.print()

    # Step 3: Search config
    roles = _setup_searches()
    console.print()

    # Step 4: scoring preferences
    _setup_preferences(profile, roles)
    console.print()

    # Step 5: AI features (optional LLM)
    _setup_ai_features()
    console.print()

    # Step 6: Manual apply helpers (Claude Code detection)
    _setup_auto_apply()
    console.print()

    # Done — show tier status
    from jobmatch.config import get_tier, TIER_LABELS, TIER_COMMANDS

    tier = get_tier()

    tier_lines: list[str] = []
    for t in sorted(TIER_LABELS):
        label = TIER_LABELS.get(t, f"Tier {t}")
        cmds = ", ".join(f"[bold]{c}[/bold]" for c in TIER_COMMANDS.get(t, []))
        if t <= tier:
            tier_lines.append(f"  [green]✓ Tier {t} — {label}[/green]  ({cmds})")
        elif t == tier + 1:
            tier_lines.append(f"  [yellow]→ Tier {t} — {label}[/yellow]  ({cmds})")
        else:
            tier_lines.append(f"  [dim]✗ Tier {t} — {label}  ({cmds})[/dim]")

    unlock_hint = ""
    if tier == 1:
        unlock_hint = "\n[dim]To unlock Tier 2: configure an LLM API key (re-run [bold]jobmatch init[/bold]).[/dim]"
    elif tier == 2:
        unlock_hint = "\n[dim]To unlock Tier 3: install Claude Code CLI + Chrome.[/dim]"

    console.print(
        Panel.fit(
            "[bold green]Setup complete![/bold green]\n\n"
            f"[bold]Your tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]\n\n"
            + "\n".join(tier_lines)
            + unlock_hint,
            border_style="green",
        )
    )
