"""Cover letter generation: LLM-powered, profile-driven, with validation.

Generates concise cover letters tailored to specific job postings. All personal
data comes from the user's profile at runtime. The prompt builder references a
configurable career headline from ~/.jobmatch/preferences.yaml so the public
source does not embed one user's private through-line/story.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from jobmatch.config import COVER_LETTER_DIR, RESUME_PATH, load_preferences, load_profile
from jobmatch.database import current_run_id, get_connection
from jobmatch.llm.client import get_client
from jobmatch.llm.parsing import strip_preamble

log = logging.getLogger(__name__)


# ── Cover letter validation (inlined from validator.py) ─────────────────

BANNED_WORDS: list[str] = [
    "passionate", "dedicated", "committed to",
    "utilizing", "utilize", "harnessing",
    "spearheaded", "spearhead", "orchestrated", "championed", "pioneered",
    "robust", "scalable solutions", "cutting-edge", "state-of-the-art", "best-in-class",
    "proven track record", "track record of success", "demonstrated ability",
    "strong communicator", "team player", "fast learner", "self-starter", "go-getter",
    "synergy", "cross-functional collaboration", "holistic",
    "transformative", "innovative solutions", "paradigm", "ecosystem",
    "proactive", "detail-oriented", "highly motivated",
    "seamless", "full lifecycle",
    "deep understanding", "extensive experience", "comprehensive knowledge",
    "thrives in", "excels at", "adept at", "well-versed in",
    "i am confident", "i believe", "i am excited",
    "plays a critical role", "instrumental in", "integral part of",
    "strong track record", "eager to", "eager",
    # Cover-letter-specific additions
    "this demonstrates", "this reflects", "i have experience with",
    "furthermore", "additionally", "moreover",
]

LLM_LEAK_PHRASES: list[str] = [
    "i am sorry", "i apologize", "i will try", "let me try",
    "i am at a loss", "i am truly sorry", "apologies for",
    "i keep fabricating", "i will have to admit", "one final attempt",
    "one last time", "if it fails again", "persistent errors",
    "i am having difficulty", "i made an error", "my mistake",
    "here is the corrected", "here is the revised", "here is the updated",
    "here is my", "below is the", "as requested",
    "note:", "disclaimer:", "important:",
    "i have rewritten", "i have removed", "i have fixed",
    "i have replaced", "i have updated", "i have corrected",
    "per your feedback", "based on your feedback", "as per the instructions",
    "the following resume", "the resume below",
    "the following cover letter", "the letter below",
]


def sanitize_text(text: str) -> str:
    """Auto-fix common LLM output issues."""
    text = text.replace(" \u2014 ", ", ").replace("\u2014", ", ")
    text = text.replace("\u2013", "-")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    return text.strip()


def validate_cover_letter(text: str, mode: str = "normal") -> dict:
    """Programmatic validation of a cover letter.

    Returns: {"passed": bool, "errors": list[str], "warnings": list[str]}
    """
    errors: list[str] = []
    warnings: list[str] = []
    text_lower = text.lower()

    if "\u2014" in text or "\u2013" in text:
        errors.append("Contains em dash or en dash.")

    if mode != "lenient":
        found = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", text_lower)]
        if found:
            msg = f"Banned words: {', '.join(found[:5])}"
            if mode == "strict":
                errors.append(msg)
            else:
                warnings.append(msg)

    words = len(text.split())
    if mode == "strict" and words > 250:
        errors.append(f"Too long ({words} words). Max 250.")
    elif mode == "normal" and words > 275:
        warnings.append(f"Long ({words} words). Target 250.")

    found_leaks = [p for p in LLM_LEAK_PHRASES if p in text_lower]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    stripped = text.strip()
    if not stripped.lower().startswith("dear"):
        errors.append("Must start with 'Dear Hiring Manager,'")

    inflated_patterns = [
        r"\b(i|we)\s+(built|created|founded|developed)\s+openclaw\b",
        r"\bopenclaw\s+(i|we)\s+(built|created|developed)\b",
        r"\bmy\s+openclaw\b",
    ]
    for pat in inflated_patterns:
        if re.search(pat, text_lower):
            errors.append("Inflated OpenClaw ownership: candidate configured and deployed agents on the OpenClaw platform, did not create it.")
            break

    return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}

MAX_ATTEMPTS = 5  # max cross-run retries before giving up


# ── Prompt Builder (profile + preferences driven) ───────────────────────

DEFAULT_HEADLINE = (
    "Connect the candidate's most relevant accomplishments into one through-line, "
    "then immediately tie it to the employer's problem."
)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _preferences_summary(preferences: dict[str, Any] | None) -> str:
    prefs = _as_dict((preferences or {}).get("scoring"))
    if not prefs:
        return "No explicit scoring preferences configured. Emphasize whatever the resume/job actually share."

    pieces: list[str] = []
    for label in ("target_roles", "adjacent_roles", "reject_roles", "positive_signals", "negative_signals"):
        items = _as_list(prefs.get(label))
        if items:
            pieces.append(f"{label}: {', '.join(items)}")
    return "\n".join(pieces)


def _build_cover_letter_prompt(profile: dict, preferences: dict[str, Any] | None = None) -> str:
    """Build the cover letter system prompt from the user's profile and preferences.

    All personal data, skills, and sign-off name come from the profile.
    The generic cover-letter strategy now comes from preferences instead of
    hardcoded candidate-specific career copy.
    """
    personal = _as_dict(profile.get("personal"))
    boundary = _as_dict(profile.get("skills_boundary"))
    resume_facts = _as_dict(profile.get("resume_facts"))
    candidate = _as_dict(preferences.get("candidate")) if preferences else {}

    # Preferred name for the sign-off (falls back to full name)
    sign_off_name = personal.get("preferred_name") or personal.get("full_name", "")

    # Flatten all allowed skills
    all_skills: list[str] = []
    for items in boundary.values():
        if isinstance(items, list):
            all_skills.extend(str(item).strip() for item in items if str(item).strip())
    skills_str = ", ".join(all_skills) if all_skills else "the tools listed in the resume"

    # Real metrics from resume_facts
    real_metrics = _as_list(resume_facts.get("real_metrics"))
    preserved_projects = _as_list(resume_facts.get("preserved_projects"))

    projects_hint = ""
    if preserved_projects:
        projects_hint = f"\nKnown projects to reference: {', '.join(preserved_projects)}"

    metrics_hint = ""
    if real_metrics:
        metrics_hint = f"\nReal metrics to use: {', '.join(real_metrics)}"

    all_banned = ", ".join(f'"{w}"' for w in BANNED_WORDS)
    leak_banned = ", ".join(f'"{p}"' for p in LLM_LEAK_PHRASES)

    headline_hint = candidate.get("headline") or DEFAULT_HEADLINE

    return f"""Write a cover letter for {sign_off_name}. The goal is to get an interview.

STRUCTURE: 3 short paragraphs. Under 250 words. Every sentence must earn its place.

PARAGRAPH 1 — THE SPINE (2-3 sentences): Open with ONE sentence that connects the candidate's career into one through-line. Strategy: {headline_hint} Then name one specific thing the candidate has done that solves THEIR problem. Never start with "I'm excited" or "This role aligns." Start with the work.

PARAGRAPH 2 — THE PROOF (3-4 sentences): Pick 2 achievements from the resume that are MOST relevant to THIS job. Use real numbers. Frame them as solving the employer's problem, not listing a CV. Rotate emphasis based on what the job actually asks for; do not assume one career lane fits every role.
{projects_hint}{metrics_hint}

PARAGRAPH 3 — THE CLOSE (1-2 sentences): One specific thing about the company from the job description (a product, a technical challenge, a team structure). Then close direct. "I'd like to show you how." or "Let's discuss." Nothing else.

CANDIDATE PREFERENCES
{_preferences_summary(preferences)}

BANNED WORDS AND PHRASES (automated validator rejects ANY of these — do not use even once):
{all_banned}

ALSO BANNED (meta-commentary the validator catches):
{leak_banned}

BANNED PUNCTUATION: No em dashes (—) or en dashes (–). Use commas or periods.

VOICE:
- Write like a real operator emailing someone they respect. Practical. Process-focused. "I get things from A to B."
- Use short sentences. Vary length. One-liners are fine. Fragments work if they land.
- NEVER narrate or explain what you're doing. BAD: "This demonstrates my commitment to X." GOOD: Just state the fact and move on.
- NEVER hedge. BAD: "might address some of your challenges." GOOD: "solves the same problem your team is facing."
- Every sentence should contain either a number, a tool name, or a specific outcome. If it doesn't, cut it.
- Read it out loud. If it sounds like a template, rewrite it. If it sounds like you wrote it at 11pm after actually doing the work, keep it.
- Don't use "I" more than 4 times. The work speaks for itself.
- If AI experience is relevant, treat it as a bonus capability unless the user's preferences or the job posting itself make it the lead.

FABRICATION = INSTANT REJECTION:
The candidate's real expertise is ONLY: {skills_str}.
Do NOT claim technical skills (coding, ML, cloud infrastructure) that aren't in this list. If the job asks for tools not listed, talk about the work actually done and the ability to learn, not the tools.

PROJECT OWNERSHIP LANGUAGE:
When referencing projects from the list below, use the EXACT verbs provided in the project description. Do NOT upgrade or inflate the language. If the project says "configured and deployed" — say "configured and deployed", NOT "built" or "created". If it says "managed" — say "managed". This is critical — the validator will reject inflated claims.

Sign off: just "{sign_off_name}"

Output ONLY the letter text. No subject lines. No "Here is the cover letter:" preamble. No notes after the sign-off.
Start DIRECTLY with "Dear Hiring Manager," and end with the name."""


# ── Core Generation ──────────────────────────────────────────────────────

def generate_cover_letter(
    resume_text: str, job: dict, profile: dict,
    max_retries: int = 3, validation_mode: str = "normal",
    preferences: dict[str, Any] | None = None,
) -> str:
    """Generate a cover letter with fresh context on each retry + auto-sanitize."""
    if preferences is None:
        preferences = load_preferences()

    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )

    avoid_notes: list[str] = []
    letter = ""
    client = get_client()
    cl_prompt_base = _build_cover_letter_prompt(profile, preferences)

    for attempt in range(max_retries + 1):
        # Fresh conversation every attempt
        prompt = cl_prompt_base
        if avoid_notes:
            prompt += "\n\n## AVOID THESE ISSUES:\n" + "\n".join(
                f"- {n}" for n in avoid_notes[-5:]
            )

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": (
                f"RESUME:\n{resume_text}\n\n---\n\n"
                f"TARGET JOB:\n{job_text}\n\n"
                "Write the cover letter:"
            )},
        ]

        letter = client.chat(messages, max_tokens=1024, temperature=0.7, stage="cover")
        letter = sanitize_text(letter)  # auto-fix em dashes, smart quotes
        letter = strip_preamble(letter)  # remove any "Here is the letter:" prefix

        validation = validate_cover_letter(letter, mode=validation_mode)
        if validation["passed"]:
            return letter

        avoid_notes.extend(validation["errors"])
        # Warnings never block — only hard errors trigger a retry
        log.debug(
            "Cover letter attempt %d/%d failed: %s",
            attempt + 1, max_retries + 1, validation["errors"],
        )

    return letter  # last attempt even if failed


# ── Batch Entry Point ────────────────────────────────────────────────────

def run_cover_letters(min_score: int = 7, limit: int = 20,
                      validation_mode: str = "normal",
                      workers: int = 5, stagger: float = 0.5) -> dict:
    """Generate cover letters for high-scoring jobs."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    profile = load_profile()
    preferences = load_preferences()
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()

    # Fetch scored jobs that don't have a cover letter yet.
    jobs = conn.execute(
        "SELECT * FROM jobs "
        "WHERE fit_score >= ? AND full_description IS NOT NULL "
        "AND (cover_letter_path IS NULL OR cover_letter_path = '') "
        "AND COALESCE(cover_attempts, 0) < ? "
        "ORDER BY fit_score DESC LIMIT ?",
        (min_score, MAX_ATTEMPTS, limit),
    ).fetchall()

    if not jobs:
        log.info("No jobs needing cover letters (score >= %d).", min_score)
        return {"generated": 0, "errors": 0, "elapsed": 0.0}

    # Convert rows to dicts
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    COVER_LETTER_DIR.mkdir(parents=True, exist_ok=True)

    # Split jobs into worker slices
    n = len(jobs)
    effective_workers = min(workers, n)
    slices = [[] for _ in range(effective_workers)]
    for i, job in enumerate(jobs):
        slices[i % effective_workers].append(job)

    log.info("Generating cover letters for %d jobs with %d workers (stagger=%.1fs)...",
             n, effective_workers, stagger)
    t0 = time.time()

    def _gen_letter(job_dict):
        """Generate one cover letter and save to disk. Returns result dict."""
        letter = generate_cover_letter(
            resume_text, job_dict, profile,
            validation_mode=validation_mode,
            preferences=preferences,
        )
        safe_title = re.sub(r"[^\w\s-]", "", job_dict["title"])[:50].strip().replace(" ", "_")
        safe_site = re.sub(r"[^\w\s-]", "", job_dict["site"])[:20].strip().replace(" ", "_")
        prefix = f"{safe_site}_{safe_title}"
        cl_path = COVER_LETTER_DIR / f"{prefix}_CL.txt"
        cl_path.write_text(letter, encoding="utf-8")

        # Generate PDF (best-effort)
        pdf_path = None
        if os.environ.get("JOBMATCH_SKIP_PDF") != "1":
            try:
                from jobmatch.scoring.pdf import convert_to_pdf
                pdf_path = str(convert_to_pdf(cl_path))
            except Exception:
                log.debug("PDF generation failed for %s", cl_path, exc_info=True)

        return {
            "url": job_dict["url"],
            "path": str(cl_path),
            "pdf_path": pdf_path,
            "title": job_dict["title"],
            "site": job_dict["site"],
            "error": None,
        }

    # Launch workers with staggered starts
    all_results: list[dict] = []
    total_errors = 0

    with ThreadPoolExecutor(max_workers=effective_workers) as pool:
        futures = {}
        for i, slice_jobs in enumerate(slices):
            if not slice_jobs:
                continue
            def _delayed_gen(sid=slice_jobs):
                time.sleep(i * stagger)
                worker_results = []
                for job in sid:
                    try:
                        result = _gen_letter(job)
                        worker_results.append(result)
                    except Exception as e:
                        worker_results.append({
                            "url": job["url"], "title": job["title"], "site": job["site"],
                            "path": None, "pdf_path": None, "error": str(e),
                        })
                return worker_results
            fut = pool.submit(_delayed_gen)
            futures[fut] = i

        for fut in as_completed(futures):
            worker_id = futures[fut]
            try:
                worker_results = fut.result()
                all_results.extend(worker_results)
                worker_ok = sum(1 for r in worker_results if r["error"] is None)
                worker_err = len(worker_results) - worker_ok
                total_errors += worker_err
                log.info("Worker %d: %d/%d OK, %d errors",
                         worker_id, worker_ok, len(worker_results), worker_err)
            except Exception as e:
                log.error("Worker %d failed: %s", worker_id, e)
                total_errors += len(slices[worker_id])

    # Write results to DB
    run_id = current_run_id()
    saved = 0
    for r in all_results:
        now = datetime.now(timezone.utc).isoformat()
        if r["error"] is None:
            conn.execute(
                "UPDATE jobs SET cover_letter_path=?, cover_letter_at=?, covered_run_id=?, "
                "cover_attempts=COALESCE(cover_attempts,0)+1 WHERE url=?",
                (r["path"], now, run_id, r["url"]),
            )
            saved += 1
        else:
            conn.execute(
                "UPDATE jobs SET cover_attempts=COALESCE(cover_attempts,0)+1 WHERE url=?",
                (r["url"],),
            )
        conn.commit()

    elapsed = time.time() - t0
    log.info("Cover letters done in %.1fs: %d generated, %d errors", elapsed, saved, total_errors)

    return {
        "generated": saved,
        "errors": total_errors,
        "elapsed": elapsed,
    }
