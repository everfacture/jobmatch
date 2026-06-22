"""Apply stage: generates self-contained apply packs for high-fit jobs.

Each pack is an HTML file with:
- Job details (title, company, location, salary)
- Cover letter
- Application URL (one-click)
- Source job posting URL

Packs are generated for jobs with cover letters that haven't been applied yet.
The user reviews and clicks through to apply manually — no CAPTCHA bypassing
or fragile form automation.
"""

import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from jobmatch.config import load_profile
from jobmatch.config.paths import RESUME_PATH, APP_DIR
from jobmatch.database import get_connection

log = logging.getLogger(__name__)

APPLY_PACK_DIR = APP_DIR / "apply-packs"


def _read_file_safe(path: str | None) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _build_apply_pack(job: dict, profile: dict) -> tuple[str, str, str]:
    """Build a self-contained HTML apply pack."""
    title = job.get("title", "Untitled")
    company = job.get("company") or job.get("site", "Unknown")
    location = job.get("location", "N/A")
    salary = job.get("salary", "Not listed")
    apply_url = job.get("application_url") or job.get("url", "")
    post_url = job.get("url", "")
    score = job.get("fit_score", "?")
    reasoning = job.get("score_reasoning", "")

    cover_letter = _read_file_safe(job.get("cover_letter_path"))
    resume_text = _read_file_safe(str(RESUME_PATH))

    personal = profile.get("personal", {})
    full_name = personal.get("full_name", "")
    email = personal.get("email", "")
    phone = personal.get("phone", "")
    linkedin = personal.get("linkedin_url", "")

    # Sanitize for safe filename
    safe_title = re.sub(r"[^\w\s-]", "", title)[:60].strip().replace(" ", "_")
    safe_company = re.sub(r"[^\w\s-]", "", company)[:30].strip().replace(" ", "_")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — {company}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 800px; margin: 0 auto; padding: 2rem; color: #1a1a1a; line-height: 1.6; }}
  .header {{ background: #f8f9fa; border-left: 4px solid #2563eb; padding: 1.5rem; margin-bottom: 2rem; border-radius: 0 8px 8px 0; }}
  .header h1 {{ margin: 0 0 0.5rem; font-size: 1.5rem; }}
  .meta {{ display: flex; gap: 1.5rem; flex-wrap: wrap; color: #64748b; font-size: 0.9rem; }}
  .score {{ display: inline-block; background: #2563eb; color: white; padding: 0.25rem 0.75rem; border-radius: 9999px; font-size: 0.85rem; font-weight: 600; }}
  .apply-btn {{ display: inline-block; background: #2563eb; color: white; padding: 0.75rem 2rem; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 1.1rem; margin: 1rem 0; }}
  .apply-btn:hover {{ background: #1d4ed8; }}
  .section {{ margin: 2rem 0; }}
  .section h2 {{ border-bottom: 2px solid #e2e8f0; padding-bottom: 0.5rem; }}
  .cover-letter {{ background: #fffbeb; border: 1px solid #fde68a; padding: 1.5rem; border-radius: 8px; white-space: pre-wrap; font-family: inherit; }}
  .resume {{ background: #f0fdf4; border: 1px solid #bbf7d0; padding: 1.5rem; border-radius: 8px; white-space: pre-wrap; }}
  .reasoning {{ color: #64748b; font-style: italic; margin: 0.5rem 0; }}
  .contact {{ color: #64748b; font-size: 0.85rem; margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #e2e8f0; }}
</style>
</head>
<body>

<div class="header">
  <h1>{title}</h1>
  <div class="meta">
    <span>{company}</span>
    <span>{location}</span>
    {f'<span>Salary: {salary}</span>' if salary != 'Not listed' else ''}
    <span class="score">Score {score}/10</span>
  </div>
  {f'<p class="reasoning">{reasoning[:200]}</p>' if reasoning else ''}
</div>

<a href="{apply_url}" target="_blank" class="apply-btn">🚀 Apply Now</a>

{f'<p><a href="{post_url}" target="_blank" style="color: #64748b;">📋 Original posting</a></p>' if post_url != apply_url else ''}

<div class="section">
  <h2>📝 Cover Letter</h2>
  <div class="cover-letter">{cover_letter or '<em>No cover letter generated.</em>'}</div>
</div>

<div class="section">
  <h2>📄 Resume</h2>
  <div class="resume">{resume_text[:3000] or '<em>No resume found.</em>'}{'...</em>' if len(resume_text) > 3000 else ''}</div>
</div>

<div class="contact">
  <strong>{full_name}</strong><br>
  {email} | {phone}<br>
  {f'<a href="{linkedin}">LinkedIn</a>' if linkedin else ''}
</div>

</body>
</html>"""

    return html, safe_company, safe_title


def run_apply_stage(min_score: int = 7, limit: int = 50) -> dict:
    """Generate apply packs for jobs with cover letters that haven't been applied.

    Each pack is a self-contained HTML file the user can open in a browser,
    review the cover letter and job details, and click through to apply.

    Args:
        min_score: Minimum fit_score threshold.
        limit: Maximum jobs to process.

    Returns:
        {"generated": int, "errors": int, "elapsed": float}
    """
    conn = get_connection()
    profile = load_profile()

    jobs = conn.execute("""
        SELECT url, title, company, site, location, fit_score, salary,
               score_reasoning, application_url, cover_letter_path
        FROM jobs
        WHERE status = 'active'
          AND fit_score >= ?
          AND cover_letter_path IS NOT NULL
          AND (applied_at IS NULL OR applied_at = '')
          AND application_url IS NOT NULL
        ORDER BY fit_score DESC, scored_at DESC
        LIMIT ?
    """, (min_score, limit)).fetchall()

    if not jobs:
        log.info("No jobs ready to apply (score >= %d).", min_score)
        return {"generated": 0, "errors": 0, "elapsed": 0.0}

    # Convert rows to dicts
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    APPLY_PACK_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Generating apply packs for %d jobs...", len(jobs))

    t0 = time.time()
    generated = 0
    error_count = 0
    now = datetime.now(timezone.utc).isoformat()

    for job in jobs:
        try:
            html, safe_company, safe_title = _build_apply_pack(job, profile)
            pack_path = APPLY_PACK_DIR / f"{safe_company}_{safe_title}.html"
            pack_path.write_text(html, encoding="utf-8")

            conn.execute(
                "UPDATE jobs SET applied_at = ?, apply_status = 'pack_generated', apply_task_id = ? WHERE url = ?",
                (now, str(pack_path), job["url"]),
            )
            conn.commit()
            generated += 1
            log.info("[%d/%d] Pack: %s — %s", generated + error_count, len(jobs), job["title"][:50], pack_path.name)
        except Exception as e:
            error_count += 1
            log.error("Failed to generate pack for %s: %s", job.get("title", "?"), e)

    elapsed = time.time() - t0
    log.info("Apply stage done in %.1fs: %d packs generated, %d errors", elapsed, generated, error_count)

    return {"generated": generated, "errors": error_count, "elapsed": elapsed}
