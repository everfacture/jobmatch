"""JobMatch Pipeline Orchestrator.

Runs pipeline stages sequentially.  Each stage reads pending rows,
processes them, writes results back to the database.

Usage (via CLI):
    jobmatch run                        # all stages
    jobmatch run discover enrich        # specific stages
    jobmatch run score                  # LLM scoring + digest
    jobmatch run --dry-run              # preview without executing
    jobmatch run --notify              # send digest after completion
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from jobmatch.config import ensure_dirs, load_env
from jobmatch.config.paths import ENV_PATH
from jobmatch.database import (
    finish_pipeline_run,
    get_stats,
    init_db,
    start_pipeline_run,
)

# Load .env for LLM credentials (JOBMATCH_LLM_*, legacy LLM_*, etc.).
load_dotenv(ENV_PATH, override=True)

log = logging.getLogger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

STAGE_ORDER = ("discover", "enrich", "score", "dedup")

STAGE_META: dict[str, dict] = {
    "discover": {"desc": "Job discovery (job boards + custom extractors)"},
    "enrich":   {"desc": "Detail enrichment (full descriptions + apply URLs)"},
    "score":    {"desc": "Hybrid scoring: deterministic rules first, LLM for ambiguous jobs"},
    "dedup":    {"desc": "Deduplicate scored jobs (keep highest score per role)"},
}


# ---------------------------------------------------------------------------
# Individual stage runners
# ---------------------------------------------------------------------------

# Simple 1-call stage definitions: (module_path, function_name).
# 'discover' is special — it has multi-sub-step logic and stays as a function.
_STAGE_ACTIONS: dict[str, tuple[str, str]] = {
    "enrich": ("jobmatch.enrichment.detail", "run_enrichment"),
    "score":  ("jobmatch.scoring.scorer", "run_scoring"),
    "dedup":  ("jobmatch.scoring.dedup", "run_dedup"),
}


def _run_stage(name: str, **kwargs: object) -> dict:
    """Import and run a single stage function, catching all errors.

    Args:
        name: Stage name matching a key in _STAGE_ACTIONS.
        **kwargs: Passed through to the stage function.

    Returns:
        {"status": "ok"} on success, {"status": "error: ..."} on failure.
    """
    mod_path, func_name = _STAGE_ACTIONS[name]
    try:
        module = __import__(mod_path, fromlist=[func_name])
        func = getattr(module, func_name)
        func(**kwargs)
        return {"status": "ok"}
    except Exception as e:
        log.error("Stage '%s' failed: %s", name, e)
        return {"status": f"error: {e}"}


def _run_discover(workers: int = 1) -> dict:
    """Stage: Job discovery — JobSpy boards, then the custom extractors."""
    stats: dict = {"jobspy": None, "smartextract": None}

    console.print("  [cyan]JobSpy crawl...[/cyan]")
    try:
        from jobmatch.discovery.jobspy import run_discovery
        stats["jobspy"] = run_discovery()
    except Exception as e:
        log.error("JobSpy crawl failed: %s", e)
        console.print(f"  [red]JobSpy error:[/red] {e}")
        stats["jobspy"] = f"error: {e}"

    console.print("  [cyan]Custom extractors...[/cyan]")
    try:
        from jobmatch.discovery.smartextract import run_smart_extract
        run_smart_extract(workers=workers)
        stats["smartextract"] = "ok"
    except Exception as e:
        log.error("Smart extract failed: %s", e)
        console.print(f"  [red]Smart extract error:[/red] {e}")
        stats["smartextract"] = f"error: {e}"

    return stats


# ---------------------------------------------------------------------------
# Stage resolution
# ---------------------------------------------------------------------------

def _resolve_stages(stage_names: list[str]) -> list[str]:
    """Resolve 'all' and validate stage names, keeping the user's order."""
    order = STAGE_ORDER

    if "all" in stage_names:
        return list(order)

    resolved = []
    for name in stage_names:
        if name not in STAGE_META:
            console.print(
                f"[red]Unknown stage:[/red] '{name}'. "
                f"Available: {', '.join(order)}, all"
            )
            raise SystemExit(1)
        if name not in resolved:
            resolved.append(name)

    # When the user explicitly selects stages, honour their selection even if
    # the stage is no longer in the default order.
    return resolved


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

def run_pipeline(
    stages: list[str] | None = None,
    min_score: int = 7,
    dry_run: bool = False,
    workers: int = 1,
    score_limit: int = 0,
    rescore: bool = False,
) -> dict:
    """Run pipeline stages sequentially.

    Args:
        stages: List of stage names, or None / ["all"] for full pipeline.
        min_score: Minimum fit score for scoring/digest stages.
        dry_run: If True, preview stages without executing.
        workers: Number of parallel threads for discovery/enrichment/scoring stages.
        score_limit: Maximum jobs to score in this run. 0 means no cap.
        rescore: If True, force re-scoring of ALL jobs (not just unscored).

    Returns:
        Dict with keys: stages (list of result dicts), errors (dict), elapsed (float).
    """
    # Resolve stages
    if stages is None:
        stages = ["all"]
    ordered = _resolve_stages(stages)

    # Banner
    console.print()
    console.print(Panel.fit(
        "[bold]JobMatch Pipeline[/bold]",
        border_style="blue",
    ))
    console.print(f"  Min score:  {min_score}")
    console.print(f"  Workers:    {workers}")
    if score_limit > 0:
        console.print(f"  Score cap:  {score_limit}")
    console.print(f"  Stages:     {' -> '.join(ordered)}")

    if dry_run:
        # Keep dry run preview usable even when no LLM provider is configured,
        # and avoid side effects like creating real runtime state.
        non_preview_stages = [s for s in ordered if s not in STAGE_META]
        if non_preview_stages:
            raise ValueError(f"Unknown stages: {non_preview_stages}")

        console.print("\n  [yellow]DRY RUN[/yellow] — would execute:")
        for name in ordered:
            meta = STAGE_META[name]
            console.print(f"    {name:<12s}  {meta['desc']}")
        console.print("\n  No changes made.")
        return {"stages": [], "errors": {}, "elapsed": 0.0}

    # Bootstrap
    load_env()
    ensure_dirs()

    from jobmatch.config.paths import APP_DIR
    from jobmatch.runlock import run_lock

    with run_lock(APP_DIR):
        init_db()

        # Pre-run stats
        pre_stats = get_stats()
        console.print(f"  DB:        {pre_stats['total']} jobs, {pre_stats['pending_detail']} pending enrichment")

        # Execute
        run_id = start_pipeline_run(ordered, min_score, workers)
        os.environ["JOBMATCH_RUN_ID"] = str(run_id)
        try:
            result = _run_sequential(
                ordered, min_score, workers=workers,
                score_limit=score_limit, rescore=rescore,
            )
            finish_pipeline_run(run_id, "error" if result.get("errors") else "ok", result)
        except Exception as e:
            finish_pipeline_run(run_id, "error", error=str(e))
            raise

    # Summary table
    console.print(f"\n{'=' * 70}")
    summary = Table(title="Pipeline Summary", show_header=True, header_style="bold")
    summary.add_column("Stage", style="bold")
    summary.add_column("Status")
    summary.add_column("Time", justify="right")

    for r in result["stages"]:
        elapsed_str = f"{r['elapsed']:.1f}s"
        status_display = r["status"][:30]
        if r["status"] == "ok":
            style = "green"
        elif r["status"] == "partial":
            style = "yellow"
        else:
            style = "red"
        summary.add_row(r["stage"], f"[{style}]{status_display}[/{style}]", elapsed_str)

    summary.add_row("", "", "")
    summary.add_row("[bold]Total[/bold]", "", f"[bold]{result['elapsed']:.1f}s[/bold]")
    console.print(summary)

    # Final DB stats
    final = get_stats()
    console.print("\n  [bold]DB Final State:[/bold]")
    console.print(f"    Total jobs:     {final['total']}")
    console.print(f"    With desc:      {final['with_description']}")
    console.print(f"    Scored:         {final['scored']}")
    console.print(f"{'=' * 70}\n")

    # --- NOTIFY HOOK ---
    if os.environ.get("JOBMATCH_NOTIFY", "").lower() in ("1", "true", "yes"):
        try:
            from jobmatch.notify import get_notifier
            notifier = get_notifier()
            report = notifier.send_digest(min_score=min_score)
            console.print(f"\n  [green]Notification:[/green] {report.summary_line()}")
        except Exception as e:
            log.warning("Notification failed: %s", e)
    # --- END NOTIFY HOOK ---

    return result


def _run_sequential(ordered: list[str], min_score: int, workers: int = 1,
                    score_limit: int = 0, rescore: bool = False) -> dict:
    """Execute stages one at a time."""
    results: list[dict] = []
    errors: dict[str, str] = {}
    pipeline_start = time.time()

    for name in ordered:
        meta = STAGE_META[name]
        console.print(f"\n{'=' * 70}")
        console.print(f"  [bold]STAGE: {name}[/bold] — {meta['desc']}")
        console.print(f"  Started: {datetime.now().strftime('%H:%M:%S')}")
        console.print(f"{'=' * 70}")

        t0 = time.time()

        try:
            kwargs: dict = {}
            if name in ("discover", "enrich"):
                kwargs["workers"] = workers
            if name == "score":
                kwargs["min_score"] = min_score
                kwargs["workers"] = workers
                kwargs["stagger"] = 0.5
            if name == "score" and score_limit > 0:
                kwargs["limit"] = score_limit
            if name == "score" and rescore:
                kwargs["rescore"] = True

            result = _run_discover(**kwargs) if name == "discover" else _run_stage(name, **kwargs)
            elapsed = time.time() - t0

            status = "ok"
            if isinstance(result, dict):
                status = result.get("status", "ok")
                if name == "discover":
                    sub_errors = [
                        f"{k}: {v}" for k, v in result.items()
                        if isinstance(v, str) and v.startswith("error")
                    ]
                    if sub_errors:
                        status = "partial"

        except Exception as e:
            elapsed = time.time() - t0
            status = f"error: {e}"
            log.exception("Stage '%s' crashed", name)
            console.print(f"\n  [red]STAGE FAILED:[/red] {e}")

        results.append({"stage": name, "status": status, "elapsed": elapsed})
        if status not in ("ok", "partial"):
            errors[name] = status

        console.print(f"\n  Stage '{name}' completed in {elapsed:.1f}s — {status}")

    total_elapsed = time.time() - pipeline_start
    return {"stages": results, "errors": errors, "elapsed": total_elapsed}
