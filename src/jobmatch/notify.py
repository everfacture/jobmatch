"""Notification adapters for JobMatch pipeline digest.

Hook: called by pipeline.py after pipeline.run() completes.
Sends a digest of jobs scored >= threshold to the configured notifier.

Adapter interface:
    class Notifier(ABC):
        def label(self) -> str: ...
        def send_digest(self, min_score: int) -> NotifyReport: ...

Built-in adapters:
    TelegramNotifier — sends individual job messages via Telegram Bot API
    ConsoleNotifier  — prints jobs to stdout (useful for testing/development)

Register new adapters by adding to the NOTIFIERS dict.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from jobmatch.config.paths import ENV_PATH
from jobmatch.database import ensure_notification_history, get_connection

# Load .env before reading config vars
load_dotenv(ENV_PATH, override=False)


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class NotifyReport:
    """Operator-facing notification counters.

    Keep Telegram delivery separate from DB rows marked notified by suppression.
    The old single integer made `sent 0` look like failure even when the
    history/dedupe path deliberately advanced rows so they would not repeat.
    """

    notifier: str
    telegram_cards_sent: int = 0
    fresh_jobs_delivered: int = 0
    history_suppressed: int = 0
    closed_marked_notified: int = 0
    failed: int = 0

    @property
    def marked_notified_without_card(self) -> int:
        return self.history_suppressed + self.closed_marked_notified

    def summary_line(self) -> str:
        return (
            f"telegram_cards_sent={self.telegram_cards_sent} "
            f"fresh_jobs_delivered={self.fresh_jobs_delivered} "
            f"history_suppressed={self.history_suppressed} "
            f"closed_marked_notified={self.closed_marked_notified} "
            f"failed={self.failed} via {self.notifier}"
        )


@dataclass(slots=True)
class PendingJobs:
    jobs: list[dict]
    history_suppressed: int = 0


# ---------------------------------------------------------------------------
# Adapter interface
# ---------------------------------------------------------------------------

class Notifier(ABC):
    """Base interface for digest delivery."""

    @abstractmethod
    def label(self) -> str:
        """Human-readable name (e.g. 'telegram', 'console')."""
        ...

    @abstractmethod
    def send_digest(self, min_score: int = 7) -> NotifyReport:
        """Send digest of high-fit jobs and return explicit counters."""
        ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _env_int(default: int, *names: str) -> int:
    """Return first valid integer env value from aliases."""
    for name in names:
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        try:
            return int(raw)
        except ValueError:
            continue
    return default


_NOTIFY_THRESHOLD = max(1, _env_int(8, "JOBMATCH_NOTIFY_THRESHOLD", "NOTIFY_THRESHOLD"))
_NOTIFY_THRESHOLD_URGENT = _env_int(9, "JOBMATCH_NOTIFY_THRESHOLD_URGENT", "NOTIFY_THRESHOLD_URGENT")


def _canonical_text(value: object) -> str:
    """Normalize text enough for notification dedupe, without fuzzy guessing."""
    text = str(value or "").lower().strip()
    text = re.sub(r"https?://", "", text)
    text = re.sub(r"[?#].*$", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def _notification_fingerprint(job: dict) -> str:
    """Stable cross-day key for the same job resurfacing under a new row/url."""
    apply_url = _canonical_text(job.get("application_url"))
    source_url = _canonical_text(job.get("url"))
    if apply_url:
        basis = f"application_url:{apply_url}"
    elif source_url:
        basis = f"url:{source_url}"
    else:
        basis = "role:" + "|".join(
            _canonical_text(job.get(key)) for key in ("title", "company", "location")
        )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _role_fingerprint(job: dict) -> str:
    """Secondary key catches reposts where the board changes URLs overnight."""
    basis = "role:" + "|".join(
        _canonical_text(job.get(key)) for key in ("title", "company", "location")
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _history_fingerprints(job: dict) -> tuple[str, str]:
    primary = _notification_fingerprint(job)
    role = _role_fingerprint(job)
    return primary, role


def _is_previously_notified(conn, job: dict) -> bool:
    ensure_notification_history(conn)
    primary, role = _history_fingerprints(job)
    row = conn.execute(
        "SELECT 1 FROM notification_history WHERE fingerprint IN (?, ?) LIMIT 1",
        (primary, role),
    ).fetchone()
    return row is not None


def _record_notification_history(conn, job: dict, notified_at: str) -> None:
    ensure_notification_history(conn)
    for fingerprint in _history_fingerprints(job):
        conn.execute(
            """
            INSERT INTO notification_history (
                fingerprint, first_notified_at, last_seen_at, url, application_url,
                title, company, location, fit_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
                last_seen_at=excluded.last_seen_at,
                url=COALESCE(excluded.url, notification_history.url),
                application_url=COALESCE(excluded.application_url, notification_history.application_url),
                title=COALESCE(excluded.title, notification_history.title),
                company=COALESCE(excluded.company, notification_history.company),
                location=COALESCE(excluded.location, notification_history.location),
                fit_score=excluded.fit_score
            """,
            (
                fingerprint,
                notified_at,
                notified_at,
                job.get("url"),
                job.get("application_url"),
                job.get("title"),
                job.get("company"),
                job.get("location"),
                job.get("fit_score"),
            ),
        )


def _backfill_notification_history(conn) -> None:
    """Seed fingerprint table from rows notified before the history table existed."""
    ensure_notification_history(conn)
    rows = conn.execute("""
        SELECT title, company, site, location, fit_score, application_url, url, notified_at
        FROM jobs
        WHERE notified_at IS NOT NULL
          AND notified_at != ''
    """).fetchall()
    for row in rows:
        job = dict(row)
        _record_notification_history(conn, job, job["notified_at"])
    conn.commit()


def _fetch_pending_jobs(min_score: int) -> PendingJobs:
    """Query DB for newly scored jobs at or above the threshold.

    Returns both actual pending jobs and rows marked notified by durable history
    suppression. That split is the operator UX fix: DB advancement is not the
    same as Telegram delivery.
    """
    conn = get_connection()
    _backfill_notification_history(conn)
    rows = conn.execute("""
        SELECT title, company, site, location, fit_score, salary,
               description, full_description,
               score_reasoning, fit_summary, gap_summary, keywords,
               application_url, url, cover_letter_path
        FROM jobs
        WHERE status = 'active'
          AND fit_score >= ?
          AND notified_at IS NULL
        ORDER BY fit_score DESC, scored_at DESC
    """, (min_score,)).fetchall()

    if not rows:
        return PendingJobs(jobs=[])

    # Deduplicate by (normalized title, company) — keep highest score per role+company
    # and suppress anything already sent in a previous digest, even if a board
    # reposted it under a fresh URL overnight.
    seen: dict[tuple[str, str], dict] = {}
    history_suppressed = 0
    for r in rows:
        job = dict(r)
        if _is_previously_notified(conn, job):
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE jobs SET notified_at = ?, notify_error = NULL WHERE url = ?",
                (now, job.get("url")),
            )
            conn.commit()
            history_suppressed += 1
            continue
        title_key = (job["title"] or "").lower().strip().rstrip("/")
        company_key = (job.get("company") or "").lower().strip()
        key = (title_key, company_key)
        if key not in seen or job["fit_score"] > seen[key]["fit_score"]:
            seen[key] = job

    return PendingJobs(
        jobs=sorted(seen.values(), key=lambda j: j["fit_score"], reverse=True),
        history_suppressed=history_suppressed,
    )


def _html_escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_DESCRIPTION_HEADING_RE = re.compile(
    r"\b(about\s+(the\s+)?company|about\s+us|company\s+description|job\s+description|"
    r"the\s+role|role\s+overview|what\s+you'?ll\s+do|responsibilities|requirements)\b\s*:?,?",
    re.IGNORECASE,
)
_DESCRIPTION_SIGNAL_RE = re.compile(
    r"\b(role|responsible|manage|lead|oversee|support|coordinate|procurement|sourcing|"
    r"supply\s+chain|logistics|vendor|supplier|category|operations|compliance|contract)\b",
    re.IGNORECASE,
)


def _clean_description_text(text: str) -> str:
    """Turn scraped Markdown/boilerplate into readable Telegram copy."""
    text = (text or "").replace("\\-", "-").replace("\\*", "*")
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)  # markdown links
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[`*_>#]+", "", text)
    text = re.sub(r"\s*[-•]\s+", " ", text)
    text = _DESCRIPTION_HEADING_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—:;,.\n\t")
    return text


def _short_description(job: dict, limit: int = 300) -> str:
    """Return a compact, human-readable job description for Telegram.

    Scraped job text often starts with Markdown headings or company boilerplate.
    Prefer the first sentence that actually describes the role; otherwise use the
    first clean sentence block.
    """
    raw = (job.get("full_description") or job.get("description") or "").strip()
    text = _clean_description_text(raw)
    if not text:
        return "No short description available. Tap the title for details."

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        return text[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + ("…" if len(text) > limit else "")

    # Use the first role-specific sentence when the posting starts with company
    # boilerplate. Include one follow-up sentence if it still fits.
    start = 0
    for idx, sentence in enumerate(sentences[:8]):
        if _DESCRIPTION_SIGNAL_RE.search(sentence):
            start = idx
            break

    selected: list[str] = []
    for sentence in sentences[start:start + 3]:
        candidate = " ".join(selected + [sentence]).strip()
        if len(candidate) > limit and selected:
            break
        selected.append(sentence)
        if len(candidate) >= 180:
            break

    summary = " ".join(selected).strip() or text
    if len(summary) <= limit:
        return summary
    return summary[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + "…"


# ---------------------------------------------------------------------------
# Pre-send job verification (check if posting is still active)
# ---------------------------------------------------------------------------

_CLOSED_INDICATORS = [
    "position closed", "no longer accepting", "job has expired",
    "position has been filled", "listing is no longer available",
    "this position is no longer available", "we are no longer accepting",
    "job expired", "closed position", "application deadline has passed",
    "application period has ended", "this role has been filled",
    "this vacancy has been closed", "position is no longer open",
]

def _is_job_still_active(url: str, timeout: int = 3) -> tuple[bool, str]:
    """Quick check if a job posting is still accepting applications.

    Returns: (is_active, reason_if_closed)
    """
    if not url:
        return True, ""  # No URL to check, assume active

    import httpx
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=timeout,
                        headers={"User-Agent": "Mozilla/5.0 (compatible; JobMatch/1.0)"})

        # HTTP status codes that mean gone
        if resp.status_code == 404:
            return False, "HTTP 404 (not found)"
        if resp.status_code == 410:
            return False, "HTTP 410 (gone)"

        # 403 = anti-bot wall (Indeed, Glassdoor, etc.), NOT a closed job
        # Assume active and let the user decide when they click
        if resp.status_code == 403:
            return True, "403 (bot-blocked, assume active)"

        # Check page content for closed indicators
        text = resp.text[:5000].lower()
        for indicator in _CLOSED_INDICATORS:
            if indicator in text:
                return False, f"closed indicator: '{indicator}'"

        return True, ""
    except Exception as e:
        # Network error — don't block, just warn
        return True, f"check failed ({type(e).__name__})"


# ---------------------------------------------------------------------------
# Compact list formatter for score-7 jobs
# ---------------------------------------------------------------------------

def _format_7_line(job: dict) -> str:
    """One-line compact entry for a score-7 job with fit/gap summary."""
    score = job.get("fit_score", 7)
    title = (job.get("title") or "Untitled")[:60]
    company = (job.get("company") or job.get("site") or "?")[:30]
    location = (job.get("location") or "?")[:25]
    url = job.get("url", "")

    # Parse fit/gap for a short summary
    parsed = _parse_reasoning(job.get("score_reasoning", ""), job)
    fit_short = ""
    if parsed["fit"]:
        # Take first 80 chars of fit as a summary
        fit_short = parsed["fit"].split("\n")[0][:80].strip()
        # Clean up bullet markers
        fit_short = fit_short.lstrip("•-– ").strip()

    # Hyperlinked title + one-liner: [Title](url) — Company · Location — fit summary
    safe_url = url.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    line = f'• <b>[{score}]</b> <a href="{safe_url}">{title}</a> — {company} · {location}'
    if fit_short:
        line += f" — {fit_short}"
    return line


def _send_7s_list(jobs: list[dict], conn) -> tuple[int, int]:
    """Send score-7 jobs as a compact grouped list, batched to 4096 chars.

    Checks each job's posting URL to verify it's still accepting applications.
    Skips closed/expired postings.
    """
    # Pre-filter: check which jobs are still active
    active_jobs = []
    closed_marked = 0
    for job in jobs:
        url = job.get("application_url") or job.get("url", "")
        is_active, close_reason = _is_job_still_active(url)
        if is_active:
            active_jobs.append(job)
        else:
            conn.execute(
                "UPDATE jobs SET notified_at = ?, notify_error = ? WHERE url = ?",
                (datetime.now(timezone.utc).isoformat(), f"posting closed: {close_reason}", job.get("url")),
            )
            closed_marked += 1
    if closed_marked:
        conn.commit()

    if not active_jobs:
        return 0, closed_marked

    header_text = f"📋 <b>Score 7 — {len(active_jobs)} jobs (compact list)</b>"
    lines = [header_text, ""]
    part = 1

    for job in active_jobs:
        line = _format_7_line(job)
        test = "\n".join(lines + [line])
        if len(test) > 3800:  # safety margin for Telegram 4096 limit
            _send_telegram_message("\n".join(lines))
            part += 1
            lines = [f"{header_text} (part {part})", ""]
        lines.append(line)

    if len(lines) > 1:
        _send_telegram_message("\n".join(lines))

    for job in active_jobs:
        notified_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE jobs SET notified_at = ?, notify_error = NULL WHERE url = ?",
            (notified_at, job.get("url")),
        )
        _record_notification_history(conn, job, notified_at)
    conn.commit()
    return len(active_jobs), closed_marked


# ---------------------------------------------------------------------------
# Telegram adapter
# ---------------------------------------------------------------------------

def _env(*names: str) -> str:
    """Return the first non-empty env value from canonical then legacy aliases."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


_BOT_TOKEN = _env("JOBMATCH_TELEGRAM_BOT_TOKEN", "BOT_TOKEN")
_CHAT_ID = _env("JOBMATCH_TELEGRAM_CHAT_ID", "CHAT_ID")
_MESSAGE_THREAD_ID = _env("JOBMATCH_TELEGRAM_THREAD_ID", "MESSAGE_THREAD_ID")


# Persistent HTTP client for Telegram (avoids 15s TLS handshake per message)
_TG_CLIENT = None

def _get_tg_client():
    global _TG_CLIENT
    if _TG_CLIENT is None:
        import httpx
        _TG_CLIENT = httpx.Client(trust_env=False, timeout=30)
    return _TG_CLIENT

def _send_telegram_message(text: str) -> bool:
    """Send a single message via Telegram Bot API. Returns True on success."""
    if not _BOT_TOKEN or not _CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
    try:
        client = _get_tg_client()
        resp = client.post(url, json={
            "chat_id": _CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "message_thread_id": int(_MESSAGE_THREAD_ID) if _MESSAGE_THREAD_ID else None,
            "disable_web_page_preview": True,
        })
        return resp.status_code == 200
    except Exception:
        return False


def _parse_reasoning(reasoning: str, job: dict | None = None) -> dict:
    """Parse structured fit/gap/keyword components from columns or legacy reasoning."""
    result = {"fit": "", "gap": "", "keywords": ""}

    if job:
        result["fit"] = (job.get("fit_summary") or "").strip()
        result["gap"] = (job.get("gap_summary") or "").strip()
        result["keywords"] = (job.get("keywords") or "").strip()

    if reasoning:
        for line in reasoning.split("\n"):
            line = line.strip()
            if line.startswith("FIT:") and not result["fit"]:
                result["fit"] = line[4:].strip()
            elif line.startswith("GAP:") and not result["gap"]:
                result["gap"] = line[4:].strip()
            elif line.startswith("KEYWORDS:") and not result["keywords"]:
                result["keywords"] = line[9:].strip()

    return result


def _format_telegram_job(job: dict) -> str:
    """Format a single high-score job as a compact job card."""
    score = job.get("fit_score", "?")
    emoji = "🔥" if score and int(score) >= _NOTIFY_THRESHOLD_URGENT else "⭐"
    title = _html_escape(job.get("title") or "Untitled")
    company = _html_escape(job.get("company") or job.get("site") or "Unknown company")
    location = _html_escape(job.get("location") or "Location unclear")
    desc = _html_escape(_short_description(job))

    apply_url = job.get("application_url", "")
    if str(apply_url).lower() in ("none", "null", ""):
        apply_url = ""
    link = apply_url or job.get("url", "")
    safe_link = _html_escape(link)

    if safe_link:
        title_line = f'{emoji} <b>[{score}]</b> <a href="{safe_link}">{title}</a>'
    else:
        title_line = f"{emoji} <b>[{score}] {title}</b>"

    lines = [
        title_line,
        f"{company} · {location}",
        "",
        desc,
    ]
    return "\n".join(lines)[:1200]


class TelegramNotifier(Notifier):
    """Sends digest via Telegram Bot API — one message per job."""

    def label(self) -> str:
        return "telegram"

    def send_digest(self, min_score: int = _NOTIFY_THRESHOLD) -> NotifyReport:
        min_score = max(min_score, _NOTIFY_THRESHOLD)
        pending = _fetch_pending_jobs(min_score)
        jobs = pending.jobs
        report = NotifyReport(notifier=self.label(), history_suppressed=pending.history_suppressed)
        if not jobs:
            return report

        conn = get_connection()

        # --- Header ---
        header = (
            f"🧠 <b>JobMatch Digest — {len(jobs)} jobs score ≥{min_score}</b>\n"
            f"Top score: {jobs[0]['fit_score']}\n"
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        _send_telegram_message(header)

        # --- High scorers only: compact individual job cards ---
        _STAGGER = [1, 3, 2, 5, 1, 4, 2, 3, 1, 5]
        _stagger_idx = 0
        for job in jobs:
            # Check if job is still accepting applications
            url = job.get("application_url") or job.get("url", "")
            is_active, close_reason = _is_job_still_active(url)
            if not is_active:
                report.closed_marked_notified += 1
                conn.execute(
                    "UPDATE jobs SET notified_at = ?, notify_error = ? WHERE url = ?",
                    (datetime.now(timezone.utc).isoformat(), f"posting closed: {close_reason}", job.get("url")),
                )
                conn.commit()
                continue

            text = _format_telegram_job(job)
            if _send_telegram_message(text):
                report.telegram_cards_sent += 1
                report.fresh_jobs_delivered += 1
                notified_at = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "UPDATE jobs SET notified_at = ?, notify_error = NULL WHERE url = ?",
                    (notified_at, job.get("url")),
                )
                _record_notification_history(conn, job, notified_at)
                conn.commit()
                time.sleep(_STAGGER[_stagger_idx % len(_STAGGER)])
                _stagger_idx += 1
            else:
                report.failed += 1
                conn.execute(
                    "UPDATE jobs SET notify_error = ? WHERE url = ?",
                    ("telegram send failed", job.get("url")),
                )
                conn.commit()

        # --- Urgent summary ---
        urgent = [j for j in jobs if j.get("fit_score", 0) >= _NOTIFY_THRESHOLD_URGENT]
        if urgent:
            urgent_text = (
                f"⚡ <b>{len(urgent)} urgent jobs (score ≥{_NOTIFY_THRESHOLD_URGENT})</b>\n"
                "High priority — review and prepare your application."
            )
            _send_telegram_message(urgent_text)

        return report


# ---------------------------------------------------------------------------
# Console adapter (for testing / development)
# ---------------------------------------------------------------------------

class ConsoleNotifier(Notifier):
    """Prints digest to stdout. Useful for testing without hitting Telegram."""

    def label(self) -> str:
        return "console"

    def send_digest(self, min_score: int = 7) -> NotifyReport:
        pending = _fetch_pending_jobs(min_score)
        jobs = pending.jobs
        report = NotifyReport(notifier=self.label(), history_suppressed=pending.history_suppressed)
        if not jobs:
            print("\n  [digest] No jobs meet threshold")
            return report

        print(f"\n  {'=' * 60}")
        print(f"  JobMatch Digest — {len(jobs)} high-fit jobs (score ≥ {min_score})")
        print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"  {'=' * 60}")

        conn = get_connection()
        for i, job in enumerate(jobs, 1):
            score = job.get("fit_score", "?")
            title = job.get("title", "Untitled")
            company = job.get("company") or job.get("site", "Unknown")
            location = job.get("location", "Unknown")
            url = job.get("url", "")

            print(f"\n  {i}. [{score}] {title}")
            print(f"     {company} | {location}")
            if url:
                print(f"     {url}")

            reasoning = job.get("score_reasoning", "")
            if reasoning:
                first_line = reasoning.split("\n")[0][:120]
                print(f"     → {first_line}")

            notified_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE jobs SET notified_at = ?, notify_error = NULL WHERE url = ?",
                (notified_at, job.get("url")),
            )
            _record_notification_history(conn, job, notified_at)
            conn.commit()
            report.telegram_cards_sent += 1
            report.fresh_jobs_delivered += 1

        print(f"\n  {'=' * 60}")
        return report


# ---------------------------------------------------------------------------
# File adapter (append to log file)
# ---------------------------------------------------------------------------

class FileNotifier(Notifier):
    """Appends digest to a file. Useful for audit logging."""

    def __init__(self, path: str | None = None):
        self.path = Path(path or os.environ.get("JOBMATCH_DIGEST_PATH", "/tmp/jobmatch-digest.log"))

    def label(self) -> str:
        return "file"

    def send_digest(self, min_score: int = 7) -> NotifyReport:
        pending = _fetch_pending_jobs(min_score)
        jobs = pending.jobs
        report = NotifyReport(notifier=self.label(), history_suppressed=pending.history_suppressed)
        if not jobs:
            return report

        conn = get_connection()
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 60}\n")
            f.write(f"JobMatch Digest — {len(jobs)} high-fit jobs (score ≥ {min_score})\n")
            f.write(f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")
            f.write(f"{'=' * 60}\n")

            for job in jobs:
                f.write(f"\n[{job.get('fit_score', '?')}] {job.get('title', 'Untitled')}\n")
                f.write(f"  {job.get('company') or job.get('site', '?')} | {job.get('location', '?')}\n")
                if job.get("url"):
                    f.write(f"  {job['url']}\n")
                reasoning = job.get("score_reasoning", "")
                if reasoning:
                    f.write(f"  → {reasoning.split(chr(10))[0][:120]}\n")

                notified_at = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "UPDATE jobs SET notified_at = ?, notify_error = NULL WHERE url = ?",
                    (notified_at, job.get("url")),
                )
                _record_notification_history(conn, job, notified_at)
                conn.commit()
                report.telegram_cards_sent += 1
                report.fresh_jobs_delivered += 1

        return report


# ---------------------------------------------------------------------------
# Notifier registry and factory
# ---------------------------------------------------------------------------

NOTIFIERS: dict[str, type[Notifier]] = {
    "telegram": TelegramNotifier,
    "console": ConsoleNotifier,
    "file": FileNotifier,
}


def get_notifier() -> Notifier:
    """Create the configured notifier.

    Selection order:
    1. JOBMATCH_NOTIFIER env var (telegram | console | file)
    2. If JOBMATCH_TELEGRAM_BOT_TOKEN/JOBMATCH_TELEGRAM_CHAT_ID are set → TelegramNotifier
       (legacy BOT_TOKEN/CHAT_ID aliases also work)
    3. Fallback → ConsoleNotifier

    Returns:
        Configured Notifier instance.
    """
    name = os.environ.get("JOBMATCH_NOTIFIER", "").lower().strip()

    if name and name in NOTIFIERS:
        return NOTIFIERS[name]()

    # Auto-detect: Telegram if credentials present, else console
    if _BOT_TOKEN and _CHAT_ID:
        return TelegramNotifier()

    return ConsoleNotifier()
