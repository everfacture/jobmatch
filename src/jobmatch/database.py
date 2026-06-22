"""JobMatch database layer.

Single source of truth for the schema. All columns from every pipeline stage
are created up front so any stage can run independently without migration
ordering issues.

Sections (in order):
  1. Connection — thread-local SQLite with WAL mode
  2. Schema — CREATE TABLE, column registry, forward migrations
  3. Queries — stats, fetch, store, count_pending
  4. Lifecycle — pipeline runs, retention pruning, status marking
"""

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from jobmatch.config import DB_PATH, PROFILE_NAME

# Thread-local connection storage — each thread gets its own connection
# (required for SQLite thread safety with parallel workers)
_local = threading.local()


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Get a thread-local cached SQLite connection with WAL mode enabled.

    Each thread gets its own connection (required for SQLite thread safety).
    Connections are cached and reused within the same thread.

    Args:
        db_path: Override the default DB_PATH. Useful for testing.

    Returns:
        sqlite3.Connection configured with WAL mode and row factory.
    """
    path = str(db_path or DB_PATH)

    if not hasattr(_local, 'connections'):
        _local.connections = {}

    conn = _local.connections.get(path)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except sqlite3.ProgrammingError:
            pass

    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    _local.connections[path] = conn
    return conn


def close_connection(db_path: Path | str | None = None) -> None:
    """Close the cached connection for the current thread."""
    path = str(db_path or DB_PATH)
    if hasattr(_local, 'connections'):
        conn = _local.connections.pop(path, None)
        if conn is not None:
            conn.close()


def init_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Create the full jobs table with all columns from every pipeline stage.

    This is idempotent -- safe to call on every startup. Uses CREATE TABLE IF NOT EXISTS
    so it won't destroy existing data.

    Schema columns by stage:
      - Discovery:  url, title, salary, description, location, site, strategy, discovered_at
      - Enrichment: full_description, application_url, detail_scraped_at, detail_error
      - Scoring:    fit_score, score_reasoning, scored_at
      - Tailoring:  tailored_resume_path, tailored_at, tailor_attempts
      - Cover:      cover_letter_path, cover_letter_at, cover_attempts
      - Apply:      applied_at, apply_status, apply_error, apply_attempts,
                   agent_id, last_attempted_at, apply_duration_ms, apply_task_id,
                   verification_confidence

    Args:
        db_path: Override the default DB_PATH.

    Returns:
        sqlite3.Connection with the schema initialized.
    """
    path = db_path or DB_PATH

    # Ensure parent directory exists
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            -- Discovery stage (smart_extract / job_search)
            url                   TEXT PRIMARY KEY,
            title                 TEXT,
            salary                TEXT,
            description           TEXT,
            location              TEXT,
            site                  TEXT,
            strategy              TEXT,
            discovered_at         TEXT,
            date_posted           TEXT,
            status                TEXT DEFAULT 'active',
            status_reason         TEXT,
            discovered_run_id     INTEGER,
            scored_run_id         INTEGER,
            tailored_run_id       INTEGER,
            covered_run_id        INTEGER,
            notified_at           TEXT,
            notify_error          TEXT,

            -- Enrichment stage (detail_scraper)
            full_description      TEXT,
            application_url       TEXT,
            detail_scraped_at     TEXT,
            detail_error          TEXT,

            -- Scoring stage (job_scorer)
            fit_score             INTEGER,
            score_reasoning       TEXT,
            fit_summary           TEXT,
            gap_summary           TEXT,
            keywords              TEXT,
            scored_at             TEXT,
            score_error           TEXT,

            -- Tailoring stage (resume tailor)
            tailored_resume_path  TEXT,
            tailored_at           TEXT,
            tailor_attempts       INTEGER DEFAULT 0,

            -- Cover letter stage
            cover_letter_path     TEXT,
            cover_letter_at       TEXT,
            cover_attempts        INTEGER DEFAULT 0,

            -- Application stage
            applied_at            TEXT,
            apply_status          TEXT,
            apply_error           TEXT,
            apply_attempts        INTEGER DEFAULT 0,
            agent_id              TEXT,
            last_attempted_at     TEXT,
            apply_duration_ms     INTEGER,
            apply_task_id         TEXT,
            verification_confidence TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_name    TEXT,
            started_at      TEXT NOT NULL,
            finished_at     TEXT,
            stages          TEXT,
            min_score       INTEGER,
            workers         INTEGER,
            status          TEXT NOT NULL DEFAULT 'running',
            error           TEXT,
            summary_json    TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at        TEXT NOT NULL,
            pipeline_run_id   INTEGER,
            stage             TEXT,
            provider_label    TEXT,
            model             TEXT,
            prompt_tokens     INTEGER,
            completion_tokens INTEGER,
            total_tokens      INTEGER,
            estimated_cost_usd REAL,
            elapsed_ms        INTEGER,
            success           INTEGER NOT NULL DEFAULT 1,
            error             TEXT
        )
    """)
    conn.commit()

    # Run migrations for any columns added after initial schema
    ensure_columns(conn)

    return conn


# Complete column registry: column_name -> SQL type with optional default.
# This is the single source of truth. Adding a column here is all that's needed
# for it to appear in both new databases and migrated ones.
_ALL_COLUMNS: dict[str, str] = {
    # Discovery
    "url": "TEXT PRIMARY KEY",
    "title": "TEXT",
    "company": "TEXT",
    "salary": "TEXT",
    "description": "TEXT",
    "location": "TEXT",
    "site": "TEXT",
    "strategy": "TEXT",
    "discovered_at": "TEXT",
    "date_posted": "TEXT",
    "status": "TEXT DEFAULT 'active'",
    "status_reason": "TEXT",
    "discovered_run_id": "INTEGER",
    "scored_run_id": "INTEGER",
    "tailored_run_id": "INTEGER",
    "covered_run_id": "INTEGER",
    "notified_at": "TEXT",
    "notify_error": "TEXT",
    # Enrichment
    "full_description": "TEXT",
    "application_url": "TEXT",
    "detail_scraped_at": "TEXT",
    "detail_error": "TEXT",
    # Scoring
    "fit_score": "INTEGER",
    "score_reasoning": "TEXT",
    "fit_summary": "TEXT",
    "gap_summary": "TEXT",
    "keywords": "TEXT",
    "scored_at": "TEXT",
    "score_error": "TEXT",
    # Tailoring
    "tailored_resume_path": "TEXT",
    "tailored_at": "TEXT",
    "tailor_attempts": "INTEGER DEFAULT 0",
    # Cover letter
    "cover_letter_path": "TEXT",
    "cover_letter_at": "TEXT",
    "cover_attempts": "INTEGER DEFAULT 0",
    # Application
    "applied_at": "TEXT",
    "apply_status": "TEXT",
    "apply_error": "TEXT",
    "apply_attempts": "INTEGER DEFAULT 0",
    "agent_id": "TEXT",
    "last_attempted_at": "TEXT",
    "apply_duration_ms": "INTEGER",
    "apply_task_id": "TEXT",
    "verification_confidence": "TEXT",
}


def ensure_columns(conn: sqlite3.Connection | None = None) -> list[str]:
    """Add any missing columns to the jobs table (forward migration).

    Reads the current table schema via PRAGMA table_info and compares against
    the full column registry. Any missing columns are added with ALTER TABLE.

    This makes it safe to upgrade the database from any previous version --
    columns are only added, never removed or renamed.

    Args:
        conn: Database connection. Uses get_connection() if None.

    Returns:
        List of column names that were added (empty if schema was already current).
    """
    if conn is None:
        conn = get_connection()

    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    added = []

    for col, dtype in _ALL_COLUMNS.items():
        if col not in existing:
            # PRIMARY KEY columns can't be added via ALTER TABLE, but url
            # is always created with the table itself so this is safe
            if "PRIMARY KEY" in dtype:
                continue
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {dtype}")
            added.append(col)

    conn.execute("UPDATE jobs SET status = 'active' WHERE status IS NULL OR status = ''")
    conn.commit()

    return added


def record_llm_usage(
    *,
    stage: str,
    provider_label: str,
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    estimated_cost_usd: float | None,
    elapsed_ms: int | None,
    success: bool = True,
    error: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Store one LLM API usage record.

    Best-effort accounting: usage logging must never break pipeline work.
    """
    if conn is None:
        conn = get_connection()
    run_id_raw = os.environ.get("JOBMATCH_RUN_ID", "").strip()
    run_id = int(run_id_raw) if run_id_raw.isdigit() else None
    conn.execute(
        """
        INSERT INTO llm_usage (
            created_at, pipeline_run_id, stage, provider_label, model,
            prompt_tokens, completion_tokens, total_tokens,
            estimated_cost_usd, elapsed_ms, success, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(), run_id, stage,
            provider_label, model, prompt_tokens, completion_tokens,
            total_tokens, estimated_cost_usd, elapsed_ms, 1 if success else 0,
            error,
        ),
    )
    conn.commit()


def get_stats(conn: sqlite3.Connection | None = None) -> dict:
    """Return job counts by pipeline stage.

    Provides a snapshot of how many jobs are at each stage, useful for
    dashboard display and pipeline progress tracking.

    Args:
        conn: Database connection. Uses get_connection() if None.

    Returns:
        Dictionary with keys:
            total, by_site, pending_detail, with_description,
            scored, unscored, tailored, untailored_eligible,
            with_cover_letter, applied, score_distribution
    """
    if conn is None:
        conn = get_connection()

    stats: dict = {}

    # Total jobs
    stats["total"] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    stats["inactive"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE status != 'active'").fetchone()[0]

    # By site breakdown
    rows = conn.execute(
        "SELECT site, COUNT(*) as cnt FROM jobs WHERE status = 'active' GROUP BY site ORDER BY cnt DESC"
    ).fetchall()
    stats["by_site"] = [(row[0], row[1]) for row in rows]

    # Enrichment stage
    stats["pending_detail"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status = 'active' AND detail_scraped_at IS NULL"
    ).fetchone()[0]

    stats["with_description"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status = 'active' AND full_description IS NOT NULL"
    ).fetchone()[0]

    stats["detail_errors"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE detail_error IS NOT NULL"
    ).fetchone()[0]

    # Scoring stage
    stats["scored"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status = 'active' AND fit_score IS NOT NULL"
    ).fetchone()[0]

    stats["unscored"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE status = 'active' AND full_description IS NOT NULL AND fit_score IS NULL"
    ).fetchone()[0]

    # Score distribution
    dist_rows = conn.execute(
        "SELECT fit_score, COUNT(*) as cnt FROM jobs "
        "WHERE status = 'active' AND fit_score IS NOT NULL "
        "GROUP BY fit_score ORDER BY fit_score DESC"
    ).fetchall()
    stats["score_distribution"] = [(row[0], row[1]) for row in dist_rows]

    # Tailoring stage
    stats["tailored"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status = 'active' AND tailored_resume_path IS NOT NULL"
    ).fetchone()[0]

    stats["untailored_eligible"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE status = 'active' AND fit_score >= 7 AND full_description IS NOT NULL "
        "AND tailored_resume_path IS NULL"
    ).fetchone()[0]

    stats["tailor_exhausted"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE status = 'active' AND COALESCE(tailor_attempts, 0) >= 5 "
        "AND tailored_resume_path IS NULL"
    ).fetchone()[0]

    # Cover letter stage
    stats["with_cover_letter"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status = 'active' AND cover_letter_path IS NOT NULL"
    ).fetchone()[0]

    stats["cover_exhausted"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE status = 'active' AND COALESCE(cover_attempts, 0) >= 5 "
        "AND (cover_letter_path IS NULL OR cover_letter_path = '')"
    ).fetchone()[0]

    # Application stage
    stats["applied"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status = 'active' AND applied_at IS NOT NULL"
    ).fetchone()[0]

    stats["apply_errors"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE apply_error IS NOT NULL"
    ).fetchone()[0]

    stats["ready_to_apply"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE status = 'active' AND cover_letter_path IS NOT NULL "
        "AND applied_at IS NULL "
        "AND application_url IS NOT NULL"
    ).fetchone()[0]

    return stats


def extract_company(description: str | None, title: str | None) -> str | None:
    """Pull company name from job description or title using heuristic patterns.

    Looks for common patterns like 'at Company', 'Company Name -',
    or the first proper noun near the top of the description.
    """

    # Try description patterns first
    if description:
        text = description[:500]  # Company name usually near top

        # Pattern: "at <Company>" — capture TitleCase words, stop at lowercase or sentence
        m = re.search(r'\bat\s+((?:[A-Z][a-zA-Z]*[\.\&]?\s*){1,6})', text)
        if m:
            candidate = m.group(1).strip().rstrip('.,')
            # Trim new sentences: ". Word" at the end
            candidate = re.sub(r'\.\s+[A-Z][a-zA-Z]+$', '', candidate).strip()
            # Trim trailing punctuation
            candidate = candidate.rstrip('.,')
            if candidate and len(candidate) > 2:
                return candidate

        # Pattern: "<Company> is hiring" or "<Company> - <role>"
        m = re.search(r'^((?:[A-Z][a-zA-Z]*[\.\&]?\s*){1,6})\s+(?:is hiring|-\s)', text)
        if m:
            return m.group(1).strip()

    # Pattern: in title: "<Role> at <Company>"
    if title:
        m = re.search(r'\bat\s+([A-Z][A-Za-z\s\.&]{2,30})$', title)
        if m:
            return m.group(1).strip()

    return None


def store_jobs(conn: sqlite3.Connection, jobs: list[dict],
               site: str, strategy: str) -> tuple[int, int]:
    """Store discovered jobs, skipping duplicates by URL.

    Args:
        conn: Database connection.
        jobs: List of job dicts with keys: url, title, company, salary, description, location.
        site: Source site name (e.g. "RemoteOK", "Dice").
        strategy: Extraction strategy used (e.g. "json_ld", "api_response", "css_selectors").

    Returns:
        Tuple of (new_count, duplicate_count).
    """
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0

    for job in jobs:
        url = job.get("url")
        if not url:
            continue
        company = job.get("company") or extract_company(
            job.get("description"), job.get("title")
        )
        try:
            conn.execute(
                "INSERT INTO jobs (url, title, company, salary, description, location, site, strategy, discovered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (url, job.get("title"), company, job.get("salary"), job.get("description"),
                 job.get("location"), site, strategy, now),
            )
            new += 1
        except sqlite3.IntegrityError:
            existing += 1

    conn.commit()
    return new, existing


# ---------------------------------------------------------------------------
# Stage conditions — single source of truth for "what's pending" SQL.
# Keys match pipeline stage names. Each entry is a WHERE clause body
# (without the SELECT / FROM). Parameters use ? placeholders.
# ---------------------------------------------------------------------------

_STAGE_CONDITIONS: dict[str, str] = {
    "enrich":  "status = 'active' AND detail_scraped_at IS NULL",
    "score":   "status = 'active' AND full_description IS NOT NULL AND fit_score IS NULL",
    "tailor":  (
        "status = 'active' AND fit_score >= ? AND full_description IS NOT NULL "
        "AND tailored_resume_path IS NULL AND COALESCE(tailor_attempts, 0) < 5"
    ),
    "cover":   (
        "status = 'active' AND fit_score >= ? AND full_description IS NOT NULL "
        "AND (cover_letter_path IS NULL OR cover_letter_path = '') "
        "AND COALESCE(cover_attempts, 0) < 5"
    ),
    # Legacy aliases for get_jobs_by_stage() callers
    "pending_detail": "status = 'active' AND detail_scraped_at IS NULL",
    "pending_score":  "status = 'active' AND full_description IS NOT NULL AND fit_score IS NULL",
    "pending_tailor": (
        "status = 'active' AND fit_score >= ? AND full_description IS NOT NULL "
        "AND tailored_resume_path IS NULL AND COALESCE(tailor_attempts, 0) < 5"
    ),
    "pending_apply":  (
        "status = 'active' AND (tailored_resume_path IS NOT NULL OR fit_score >= ?) "
        "AND applied_at IS NULL AND application_url IS NOT NULL"
    ),
}


def count_pending(stage: str, conn: sqlite3.Connection | None = None,
                  min_score: int = 7) -> int:
    """Count pending work items for a pipeline stage.

    Args:
        stage: Pipeline stage name (enrich, score, tailor, cover, pdf).
        conn: Database connection. Uses get_connection() if None.
        min_score: Minimum fit_score for tailor stage.

    Returns:
        Number of jobs pending this stage.
    """
    if conn is None:
        conn = get_connection()
    where = _STAGE_CONDITIONS.get(stage)
    if where is None:
        raise ValueError(f"Unknown stage: {stage}")
    sql = f"SELECT COUNT(*) FROM jobs WHERE {where}"
    if "?" in where:
        return conn.execute(sql, (min_score,)).fetchone()[0]
    return conn.execute(sql).fetchone()[0]


def get_jobs_by_stage(conn: sqlite3.Connection | None = None,
                      stage: str = "discovered",
                      min_score: int | None = None,
                      limit: int = 100) -> list[dict]:
    """Fetch jobs filtered by pipeline stage.

    Args:
        conn: Database connection. Uses get_connection() if None.
        stage: One of "discovered", "enriched", "scored", "tailored", "applied".
        min_score: Minimum fit_score filter (only relevant for scored+ stages).
        limit: Maximum number of rows to return.

    Returns:
        List of job dicts.
    """
    if conn is None:
        conn = get_connection()

    # High-level stage views (snapshot-style)
    conditions = {
        "discovered": "status = 'active'",
        "enriched":   "status = 'active' AND full_description IS NOT NULL",
        "scored":     "status = 'active' AND fit_score IS NOT NULL",
        "tailored":   "status = 'active' AND tailored_resume_path IS NOT NULL",
        "prepped":    "status = 'active' AND cover_letter_path IS NOT NULL",
        "applied":    "status = 'active' AND applied_at IS NOT NULL",
    }
    # Merge with pending-work conditions from _STAGE_CONDITIONS
    conditions.update(_STAGE_CONDITIONS)

    where = conditions.get(stage, "1=1")
    if where == "1=1":
        raise ValueError(f"Unknown stage: {stage}")
    params: list = []

    if "?" in where and min_score is not None:
        params.append(min_score)
    elif "?" in where:
        params.append(7)  # default min_score

    if min_score is not None and "fit_score" not in where and stage in ("scored", "tailored", "applied"):
        where += " AND fit_score >= ?"
        params.append(min_score)

    query = f"SELECT * FROM jobs WHERE {where} ORDER BY fit_score DESC NULLS LAST, discovered_at DESC"
    if limit > 0:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()

    # Convert sqlite3.Row objects to dicts
    if rows:
        columns = rows[0].keys()
        return [dict(zip(columns, row)) for row in rows]
    return []


# ---------------------------------------------------------------------------
# Pipeline run state, lifecycle, and retention
# ---------------------------------------------------------------------------

def start_pipeline_run(stages: list[str], min_score: int, workers: int,
                       conn: sqlite3.Connection | None = None) -> int:
    """Create a durable record for one pipeline execution."""
    if conn is None:
        conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO pipeline_runs (profile_name, started_at, stages, min_score, workers, status)
        VALUES (?, ?, ?, ?, ?, 'running')
        """,
        (PROFILE_NAME, now, ",".join(stages), min_score, workers),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_pipeline_run(run_id: int, status: str, summary: dict | None = None,
                        error: str | None = None,
                        conn: sqlite3.Connection | None = None) -> None:
    """Mark a pipeline run completed or failed."""
    if conn is None:
        conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE pipeline_runs
        SET finished_at = ?, status = ?, error = ?, summary_json = ?
        WHERE id = ?
        """,
        (now, status, error, json.dumps(summary or {}, ensure_ascii=False), run_id),
    )
    conn.commit()


def mark_jobs_status(conn: sqlite3.Connection, status: str, reason: str, where: str,
                     params: tuple = ()) -> int:
    """Mark jobs with lifecycle status instead of deleting evidence immediately."""
    cur = conn.execute(
        f"UPDATE jobs SET status = ?, status_reason = ? WHERE {where}",
        (status, reason, *params),
    )
    conn.commit()
    return cur.rowcount


def current_run_id() -> int | None:
    """Return active pipeline run id from environment, if set."""
    raw = os.environ.get("JOBMATCH_RUN_ID", "").strip()
    return int(raw) if raw.isdigit() else None


def prune_jobs(days: int = 14, statuses: tuple[str, ...] = ("low_score", "duplicate", "expired", "stale", "url_merged"),
               dry_run: bool = True, expire_active: bool = True,
               conn: sqlite3.Connection | None = None) -> int:
    """Delete old inactive jobs after retention window.

    When expire_active is true, old active jobs are first marked expired so
    stale unavailable postings are removed by the same retention policy.
    """
    if conn is None:
        conn = get_connection()
    cutoff = f"-{days} days"
    if expire_active:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'expired', status_reason = ?
            WHERE status = 'active' AND COALESCE(discovered_at, scored_at) < datetime('now', ?)
            """,
            (f"older than {days} days", cutoff),
        )
        conn.commit()
    placeholders = ",".join("?" for _ in statuses)
    sql = (
        f"FROM jobs WHERE status IN ({placeholders}) "
        "AND COALESCE(scored_at, discovered_at) < datetime('now', ?)"
    )
    params = (*statuses, cutoff)
    count = conn.execute(f"SELECT COUNT(*) {sql}", params).fetchone()[0]
    if not dry_run and count:
        conn.execute(f"DELETE {sql}", params)
        conn.commit()
    return int(count)
