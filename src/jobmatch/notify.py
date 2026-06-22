"""Notification adapters for JobMatch pipeline digest.

Hook: called by pipeline.py after pipeline.run() completes.
Sends a digest of jobs scored >= threshold to the configured notifier.

Adapter interface:
    class Notifier(ABC):
        def label(self) -> str: ...
        def send_digest(self, jobs: list[dict], min_score: int) -> int: ...

Built-in adapters:
    TelegramNotifier — sends individual job messages via Telegram Bot API
    ConsoleNotifier  — prints jobs to stdout (useful for testing/development)

Register new adapters by adding to the NOTIFIERS dict.
"""

import os
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from jobmatch.config.paths import ENV_PATH
from jobmatch.database import get_connection

# Load .env before reading config vars
load_dotenv(ENV_PATH, override=True)


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
    def send_digest(self, min_score: int = 7) -> int:
        """Send digest of high-fit jobs.

        Args:
            min_score: Minimum fit_score threshold.

        Returns:
            Number of jobs successfully sent.
        """
        ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_NOTIFY_THRESHOLD = max(8, int(os.environ.get("NOTIFY_THRESHOLD", "8")))
_NOTIFY_THRESHOLD_URGENT = int(os.environ.get("NOTIFY_THRESHOLD_URGENT", "9"))


def _fetch_pending_jobs(min_score: int) -> list[dict]:
    """Query DB for newly scored jobs at or above the threshold.

    NOTE: Does NOT require cover_letter_path — fetches ALL scored jobs.
    Cover letters are optional; score-7 jobs don't need them.
    """
    conn = get_connection()
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
        return []

    # Deduplicate by (normalized title, company) — keep highest score per role+company
    seen: dict[tuple[str, str], dict] = {}
    for r in rows:
        job = dict(r)
        title_key = (job["title"] or "").lower().strip().rstrip("/")
        company_key = (job.get("company") or "").lower().strip()
        key = (title_key, company_key)
        if key not in seen or job["fit_score"] > seen[key]["fit_score"]:
            seen[key] = job

    return sorted(seen.values(), key=lambda j: j["fit_score"], reverse=True)


def _html_escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_DESCRIPTION_HEADING_RE = re.compile(
    r"\b(about\s+(the\s+)?company|about\s+us|company\s+description|job\s+description|"
    r"the\s+role|role\s+overview|what\s+you'?ll\s+do|responsibilities|requirements)\b\s*:?",
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


def _send_7s_list(jobs: list[dict], conn) -> int:
    """Send score-7 jobs as a compact grouped list, batched to 4096 chars.
    
    Checks each job's posting URL to verify it's still accepting applications.
    Skips closed/expired postings.
    """
    # Pre-filter: check which jobs are still active
    active_jobs = []
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
    if active_jobs:
        conn.commit()

    if not active_jobs:
        return 0

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
        conn.execute(
            "UPDATE jobs SET notified_at = ?, notify_error = NULL WHERE url = ?",
            (datetime.now(timezone.utc).isoformat(), job.get("url")),
        )
    conn.commit()
    return len(jobs)


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

    def send_digest(self, min_score: int = _NOTIFY_THRESHOLD) -> int:
        min_score = max(min_score, _NOTIFY_THRESHOLD)
        jobs = _fetch_pending_jobs(min_score)
        if not jobs:
            return 0

        conn = get_connection()
        sent = 0

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
        _skipped_closed = 0
        for job in jobs:
            # Check if job is still accepting applications
            url = job.get("application_url") or job.get("url", "")
            is_active, close_reason = _is_job_still_active(url)
            if not is_active:
                _skipped_closed += 1
                conn.execute(
                    "UPDATE jobs SET notified_at = ?, notify_error = ? WHERE url = ?",
                    (datetime.now(timezone.utc).isoformat(), f"posting closed: {close_reason}", job.get("url")),
                )
                conn.commit()
                continue

            text = _format_telegram_job(job)
            if _send_telegram_message(text):
                sent += 1
                conn.execute(
                    "UPDATE jobs SET notified_at = ?, notify_error = NULL WHERE url = ?",
                    (datetime.now(timezone.utc).isoformat(), job.get("url")),
                )
                conn.commit()
                time.sleep(_STAGGER[_stagger_idx % len(_STAGGER)])
                _stagger_idx += 1
            else:
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

        return sent


# ---------------------------------------------------------------------------
# Console adapter (for testing / development)
# ---------------------------------------------------------------------------

class ConsoleNotifier(Notifier):
    """Prints digest to stdout. Useful for testing without hitting Telegram."""

    def label(self) -> str:
        return "console"

    def send_digest(self, min_score: int = 7) -> int:
        jobs = _fetch_pending_jobs(min_score)
        if not jobs:
            print("\n  [digest] No jobs meet threshold")
            return 0

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

            conn.execute(
                "UPDATE jobs SET notified_at = ?, notify_error = NULL WHERE url = ?",
                (datetime.now(timezone.utc).isoformat(), job.get("url")),
            )
            conn.commit()

        print(f"\n  {'=' * 60}")
        return len(jobs)


# ---------------------------------------------------------------------------
# File adapter (append to log file)
# ---------------------------------------------------------------------------

class FileNotifier(Notifier):
    """Appends digest to a file. Useful for audit logging."""

    def __init__(self, path: str | None = None):
        self.path = Path(path or os.environ.get("JOBMATCH_DIGEST_PATH", "/tmp/jobmatch-digest.log"))

    def label(self) -> str:
        return "file"

    def send_digest(self, min_score: int = 7) -> int:
        jobs = _fetch_pending_jobs(min_score)
        if not jobs:
            return 0

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

                conn.execute(
                    "UPDATE jobs SET notified_at = ?, notify_error = NULL WHERE url = ?",
                    (datetime.now(timezone.utc).isoformat(), job.get("url")),
                )
                conn.commit()

        return len(jobs)


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
