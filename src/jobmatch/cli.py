"""JobMatch CLI — the main entry point."""

from __future__ import annotations

import logging
import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from jobmatch import __version__

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)

app = typer.Typer(
    name="jobmatch",
    help="AI-powered job discovery pipeline — discover, enrich, score, deduplicate, and deliver digests.",
    no_args_is_help=True,
)
console = Console()
log = logging.getLogger(__name__)


def _set_profile(profile: str | None) -> None:
    """Select active candidate profile before importing config modules."""
    if profile:
        os.environ["JOBMATCH_PROFILE"] = profile


# Valid pipeline stages (in execution order)
VALID_STAGES = ("discover", "enrich", "score", "tailor", "cover", "dedup", "apply")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bootstrap() -> None:
    """Common setup: load env, create dirs, init DB."""
    from jobmatch.config import load_env, ensure_dirs
    from jobmatch.database import init_db

    load_env()
    ensure_dirs()
    init_db()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold]jobmatch[/bold] {__version__}")
        raise typer.Exit()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        help="Candidate profile name. Named profiles use ~/.jobmatch/profiles/<name>.",
    ),
) -> None:
    """JobMatch — AI-powered job discovery, scoring, and digest pipeline."""
    _set_profile(profile)


@app.command()
def init() -> None:
    """Run the first-time setup wizard (profile, resume, search config)."""
    from jobmatch.wizard.init import run_wizard

    run_wizard()


@app.command()
def run(
    stages: Optional[list[str]] = typer.Argument(
        None,
        help=(
            "Pipeline stages to run. "
            f"Valid: {', '.join(VALID_STAGES)}, all. "
            "Defaults to 'all' if omitted."
        ),
    ),
    min_score: int = typer.Option(7, "--min-score", help="Minimum fit score for scoring/digest stages."),
    workers: int = typer.Option(5, "--workers", "-w", help="Parallel threads for discovery/enrichment/scoring."),
    rescore: bool = typer.Option(False, "--rescore", help="Force re-scoring of ALL jobs (not just unscored)."),
    with_resume: bool = typer.Option(False, "--with-resume", help="Include tailor stage (generates full PDF resumes)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview stages without executing."),
    validation: str = typer.Option(
        "normal",
        "--validation",
        help=(
            "Validation strictness for tailor/cover stages when run explicitly. "
            "strict: banned words = errors, judge must pass. "
            "normal: banned words = warnings only (default, recommended for Gemini free tier). "
            "lenient: banned words ignored, LLM judge skipped (fastest, fewest API calls)."
        ),
    ),
    notify: bool = typer.Option(False, "--notify", help="Send digest to Telegram after completion."),
    no_notify: bool = typer.Option(False, "--no-notify", help="Do not auto-send digest after scoring."),
) -> None:
    """Run pipeline stages: discover, enrich, score, dedup, then send digest."""
    from jobmatch.pipeline import run_pipeline

    stage_list = stages if stages else ["all"]

    # Validate stage names
    for s in stage_list:
        if s != "all" and s not in VALID_STAGES:
            console.print(
                f"[red]Unknown stage:[/red] '{s}'. "
                f"Valid stages: {', '.join(VALID_STAGES)}, all"
            )
            raise typer.Exit(code=1)

    # Gate AI stages behind Tier 2. Dry run is only a preview and must work
    # before users have configured an LLM provider.
    llm_stages = {"score", "tailor", "cover"}
    if not dry_run and (any(s in stage_list for s in llm_stages) or "all" in stage_list):
        from jobmatch.config import check_tier
        check_tier(2, "AI scoring/tailoring")

    # Validate the --validation flag value
    valid_modes = ("strict", "normal", "lenient")
    if validation not in valid_modes:
        console.print(
            f"[red]Invalid --validation value:[/red] '{validation}'. "
            f"Choose from: {', '.join(valid_modes)}"
        )
        raise typer.Exit(code=1)

    # Auto-enable notify for full pipeline runs or any run with score unless explicitly disabled.
    if no_notify:
        os.environ.pop("JOBMATCH_NOTIFY", None)
    elif not notify and (not stages or stages == ["all"] or "score" in stages):
        notify = True

    if notify and not no_notify:
        os.environ["JOBMATCH_NOTIFY"] = "1"

    result = run_pipeline(
        stages=stage_list,
        min_score=min_score,
        dry_run=dry_run,
        workers=workers,
        validation_mode=validation,
        rescore=rescore,
        with_resume=with_resume,
    )

    exit_code = 1 if result.get("errors") else 0

    # Some scraper libraries can leave non-daemon background threads alive after
    # the useful work has finished. Cron cares about process exit, not vibes.
    # Opt-in hard exit after run keeps discovery chunks from timing out after
    # they have already written results and closed the pipeline_run cleanly.
    if os.environ.get("JOBMATCH_EXIT_AFTER_RUN", "").lower() in ("1", "true", "yes"):
        import sys
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(exit_code)

    if exit_code:
        raise typer.Exit(code=exit_code)


@app.command()
def status() -> None:
    """Show pipeline statistics from the database."""
    _bootstrap()

    from jobmatch.database import get_stats

    stats = get_stats()

    console.print("\n[bold]JobMatch Pipeline Status[/bold]\n")

    # Summary table
    summary = Table(title="Pipeline Overview", show_header=True, header_style="bold cyan")
    summary.add_column("Metric", style="bold")
    summary.add_column("Count", justify="right")

    summary.add_row("Total jobs discovered", str(stats["total"]))
    summary.add_row("With full description", str(stats["with_description"]))
    summary.add_row("Pending enrichment", str(stats["pending_detail"]))
    summary.add_row("Enrichment errors", str(stats["detail_errors"]))
    summary.add_row("Scored", str(stats["scored"]))
    summary.add_row("Pending scoring", str(stats["unscored"]))
    summary.add_row("Cover letters", str(stats["with_cover_letter"]))
    summary.add_row("Cover pending (scored, no cover yet)", str(stats["cover_exhausted"]))
    summary.add_row("Inactive jobs", str(stats.get("inactive", 0)))

    console.print(summary)

    # Score distribution
    if stats["score_distribution"]:
        dist_table = Table(title="\nScore Distribution", show_header=True, header_style="bold yellow")
        dist_table.add_column("Score", justify="center")
        dist_table.add_column("Count", justify="right")
        dist_table.add_column("Bar")

        max_count = max(count for _, count in stats["score_distribution"]) or 1
        for score, count in stats["score_distribution"]:
            bar_len = int(count / max_count * 30)
            if score >= 7:
                color = "green"
            elif score >= 5:
                color = "yellow"
            else:
                color = "red"
            bar = f"[{color}]{'=' * bar_len}[/{color}]"
            dist_table.add_row(str(score), str(count), bar)

        console.print(dist_table)

    # By site
    if stats["by_site"]:
        site_table = Table(title="\nJobs by Source", show_header=True, header_style="bold magenta")
        site_table.add_column("Site")
        site_table.add_column("Count", justify="right")

        for site, count in stats["by_site"]:
            site_table.add_row(site or "Unknown", str(count))

        console.print(site_table)

    console.print()


@app.command()
def prune(
    older_than: int = typer.Option(14, "--older-than", help="Retention window in days."),
    dry_run: bool = typer.Option(True, "--dry-run/--yes", help="Preview deletions unless --yes is passed."),
) -> None:
    """Hard-delete inactive jobs older than the retention window."""
    _bootstrap()

    from jobmatch.database import prune_jobs

    count = prune_jobs(days=older_than, dry_run=dry_run)
    action = "Would delete" if dry_run else "Deleted"
    console.print(f"{action} {count} inactive jobs older than {older_than} days")


@app.command()
def dashboard() -> None:
    """Generate and open the HTML dashboard in your browser."""
    _bootstrap()

    from jobmatch.view import open_dashboard

    open_dashboard()


@app.command()
def doctor() -> None:
    """Check your setup and diagnose missing requirements."""
    from jobmatch.config import (
        load_env, PROFILE_PATH, RESUME_PATH, RESUME_PDF_PATH,
        SEARCH_CONFIG_PATH,
    )

    load_env()

    ok_mark = "[green]OK[/green]"
    fail_mark = "[red]MISSING[/red]"
    warn_mark = "[yellow]WARN[/yellow]"

    results: list[tuple[str, str, str]] = []  # (check, status, note)

    # --- Tier 1 checks ---
    # Profile
    if PROFILE_PATH.exists():
        results.append(("profile.json", ok_mark, str(PROFILE_PATH)))
    else:
        results.append(("profile.json", fail_mark, "Run 'jobmatch init' to create"))

    # Resume
    if RESUME_PATH.exists():
        results.append(("resume.txt", ok_mark, str(RESUME_PATH)))
    elif RESUME_PDF_PATH.exists():
        results.append(("resume.txt", warn_mark, "Only PDF found — plain-text needed for AI stages"))
    else:
        results.append(("resume.txt", fail_mark, "Run 'jobmatch init' to add your resume"))

    # Search config
    if SEARCH_CONFIG_PATH.exists():
        results.append(("searches.yaml", ok_mark, str(SEARCH_CONFIG_PATH)))
    else:
        results.append(("searches.yaml", warn_mark, "Will use example config — run 'jobmatch init'"))

    # jobspy (job board discovery dependency)
    try:
        import jobspy  # noqa: F401
        results.append(("python-jobspy", ok_mark, "Job board scraping available"))
    except ImportError:
        results.append(("python-jobspy", fail_mark,
                        "Install package dependencies: pip install -e '.[dev]'"))

    # --- Tier 2 checks ---
    try:
        from jobmatch.llm.client import configured_provider_labels
        provider_labels = configured_provider_labels()
    except RuntimeError:
        provider_labels = []

    if provider_labels:
        results.append(("LLM provider", ok_mark, " -> ".join(provider_labels)))
    else:
        results.append((
            "LLM provider",
            fail_mark,
            "Set JOBMATCH_LLM_BASE_URL + JOBMATCH_LLM_API_KEY in ~/.jobmatch/.env",
        ))

    # CapSolver (optional — for enrichment tier 3)
    capsolver = os.environ.get("CAPSOLVER_API_KEY")
    if capsolver:
        results.append(("CapSolver API key", ok_mark, "CAPTCHA solving enabled"))
    else:
        results.append(("CapSolver API key", "[dim]optional[/dim]",
                        "Set CAPSOLVER_API_KEY in .env for CAPTCHA solving"))

    # --- Render results ---
    console.print()
    console.print("[bold]JobMatch Doctor[/bold]\n")

    col_w = max(len(r[0]) for r in results) + 2
    for check, status, note in results:
        pad = " " * (col_w - len(check))
        console.print(f"  {check}{pad}{status}  [dim]{note}[/dim]")

    console.print()

    # Tier summary
    from jobmatch.config import get_tier, TIER_LABELS
    tier = get_tier()
    console.print(f"[bold]Current tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]")

    if tier == 1:
        console.print("[dim]  → Tier 2 unlocks: AI scoring (needs LLM API key)[/dim]")

    console.print()


if __name__ == "__main__":
    app()
