"""Job fit scoring: LLM-powered evaluation of candidate-job match quality.

Scores jobs on a 1-10 scale by comparing the user's resume, profile, and
runtime scoring preferences against each job description. Public source stays
candidate-neutral; personal search strategy belongs in ~/.jobmatch/preferences.yaml.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from jobmatch.config import RESUME_PATH, load_preferences, load_profile, scoring_preferences
from jobmatch.config.paths import ENV_PATH
from jobmatch.database import current_run_id, get_connection, get_jobs_by_stage
from jobmatch.llm.client import get_client
from jobmatch.llm.parsing import parse_marker_lines
from jobmatch.scoring.rules import apply_configured_hard_caps, score_by_rules

# Load .env for LLM credentials
load_dotenv(ENV_PATH, override=True)

log = logging.getLogger(__name__)


# ── Prompt building ────────────────────────────────────────────────────────

def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _join_list(value: Any, fallback: str = "not specified") -> str:
    items = _as_list(value)
    return ", ".join(items) if items else fallback


def _profile_summary(profile: dict[str, Any]) -> str:
    personal = _as_dict(profile.get("personal"))
    experience = _as_dict(profile.get("experience"))
    skills = _as_dict(profile.get("skills_boundary"))
    resume_facts = _as_dict(profile.get("resume_facts"))

    name = personal.get("preferred_name") or personal.get("full_name") or "the candidate"
    target_role = experience.get("target_role") or experience.get("current_title") or "not specified"
    current_title = experience.get("current_title") or experience.get("current_job_title") or "not specified"
    years = experience.get("years_of_experience_total") or "not specified"
    education = experience.get("education_level") or "not specified"

    skill_bits: list[str] = []
    for label, value in skills.items():
        items = _as_list(value)
        if items:
            skill_bits.append(f"{label}: {', '.join(items)}")

    fact_bits: list[str] = []
    for key in ("preserved_companies", "preserved_projects", "real_metrics"):
        items = _as_list(resume_facts.get(key))
        if items:
            fact_bits.append(f"{key}: {', '.join(items)}")
    if resume_facts.get("preserved_school"):
        fact_bits.append(f"preserved_school: {resume_facts['preserved_school']}")

    return "\n".join([
        f"Name/reference: {name}",
        f"Target role: {target_role}",
        f"Current/recent title: {current_title}",
        f"Years of experience: {years}",
        f"Education: {education}",
        f"Skills: {'; '.join(skill_bits) if skill_bits else 'not specified'}",
        f"Resume facts to respect: {'; '.join(fact_bits) if fact_bits else 'not specified'}",
    ])


def _preferences_summary(preferences: dict[str, Any] | None) -> str:
    prefs = scoring_preferences(preferences)
    if not prefs:
        return "No explicit scoring preferences configured. Judge fit from the resume/profile and the job posting."

    hard_caps = []
    for cap in prefs.get("hard_caps", []) or []:
        if isinstance(cap, dict):
            name = cap.get("name") or "unnamed"
            max_score = cap.get("max_score", "?")
            patterns = _join_list(cap.get("patterns"), fallback="no patterns")
            hard_caps.append(f"{name}: max {max_score} when matching {patterns}")

    return "\n".join([
        f"Target roles: {_join_list(prefs.get('target_roles'))}",
        f"Adjacent roles: {_join_list(prefs.get('adjacent_roles'))}",
        f"Rejected roles: {_join_list(prefs.get('reject_roles'))}",
        f"Dealbreakers: {_join_list(prefs.get('dealbreakers'))}",
        f"Positive signals: {_join_list(prefs.get('positive_signals'))}",
        f"Negative signals: {_join_list(prefs.get('negative_signals'))}",
        f"Hard caps: {'; '.join(hard_caps) if hard_caps else 'not specified'}",
    ])


def build_score_prompt(profile: dict[str, Any], preferences: dict[str, Any] | None = None) -> str:
    """Build the candidate-neutral scoring system prompt."""
    return f"""You are a job-fit evaluator for a specific candidate.

Score the job 1-10 based only on the candidate's actual resume/profile, the configured preferences, and the job posting.

CANDIDATE PROFILE
{_profile_summary(profile)}

SCORING PREFERENCES
{_preferences_summary(preferences)}

SCORING RULES
- 9-10: Excellent match for the candidate's target roles, experience, constraints, and preferences.
- 7-8: Plausible/strong match with some gaps or adjacent-role fit.
- 5-6: Possible but weak, missing important requirements, or only partially aligned.
- 1-4: Clear mismatch, rejected role, dealbreaker, hard-cap violation, or requires experience the candidate does not have.
- Apply configured hard caps and dealbreakers strictly.
- Do not invent experience, credentials, locations, citizenship, salary, or metrics.
- Remote/hybrid is a plus only when it matches the candidate's preferences or constraints.
- If preferences are absent or incomplete, rely on the resume/profile and state uncertainty in GAP.

RESPOND IN EXACTLY THIS FORMAT (no other text):
SCORE: [1-10]
FIT: [2-3 short bullets explaining why this role fits the candidate — cite actual resume/profile/job evidence]
GAP: [1-2 short bullets on what's missing, risky, or uncertain]
KEYWORDS: [comma-separated ATS keywords from the job description that match or could match the candidate]"""


# ── Response parsing ───────────────────────────────────────────────────────

def _parse_score_response(response: str) -> dict:
    """Parse the LLM's score response into structured data."""
    parsed = parse_marker_lines(response, {
        "SCORE": int, "FIT": str, "GAP": str, "KEYWORDS": str,
    })
    score = parsed.get("SCORE")
    if score is not None:
        score = max(1, min(10, score))
    return {
        "score": score,
        "fit": parsed.get("FIT", ""),
        "gap": parsed.get("GAP", ""),
        "keywords": parsed.get("KEYWORDS", ""),
        "error": None if score is not None else f"Failed to parse score from response: {response[:200]}",
    }


def score_job(
    resume_text: str,
    job: dict,
    profile: dict[str, Any] | None = None,
    preferences: dict[str, Any] | None = None,
) -> dict:
    """Score a single job against the candidate profile/resume/preferences."""
    if profile is None:
        profile = load_profile()
    if preferences is None:
        preferences = load_preferences()

    # Rules-first gate: deterministic scoring for obvious configured cases.
    # Ambiguous roles still go to the LLM.
    rule_result = score_by_rules(job, preferences)
    if rule_result is not None:
        rule_result["url"] = job.get("url", "")
        return rule_result

    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job.get('company', '')}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )

    messages = [
        {"role": "system", "content": build_score_prompt(profile, preferences)},
        {"role": "user", "content": f"RESUME:\n{resume_text}\n\n---\n\nJOB POSTING:\n{job_text}"},
    ]

    try:
        client = get_client()
        response = client.chat(messages, max_tokens=512, temperature=0.2, stage="score")
        result = _parse_score_response(response)
        return apply_configured_hard_caps(result, job, preferences)
    except Exception as e:
        log.error("LLM error scoring job '%s': %s", job.get("title", "?"), e)
        return {"score": None, "fit": "", "gap": "", "keywords": "", "error": str(e)}


def run_scoring(limit: int = 0, rescore: bool = False, min_score: int = 7,
                workers: int = 5, stagger: float = 0.5) -> dict:
    """Score unscored jobs that have full descriptions.

    Args:
        limit: Maximum number of jobs to score in this run.
        rescore: If True, re-score all jobs (not just unscored ones).
        min_score: Jobs scoring below this are deleted (they'll never advance).
        workers: Number of parallel scoring workers.
        stagger: Seconds to delay each worker start (avoids concurrency cap).

    Returns:
        {"scored": int, "errors": int, "pruned": int, "elapsed": float, "distribution": list}
    """
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    profile = load_profile()
    preferences = load_preferences()
    conn = get_connection()

    if rescore:
        query = "SELECT * FROM jobs WHERE full_description IS NOT NULL"
        if limit > 0:
            query += f" LIMIT {limit}"
        jobs = conn.execute(query).fetchall()
    else:
        jobs = get_jobs_by_stage(conn=conn, stage="pending_score", limit=limit)

    if not jobs:
        log.info("No unscored jobs with descriptions found.")
        return {"scored": 0, "errors": 0, "elapsed": 0.0, "distribution": []}

    # Convert sqlite3.Row to dicts if needed
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    # Split jobs into worker slices
    n = len(jobs)
    effective_workers = min(workers, n)
    slices = [[] for _ in range(effective_workers)]
    for i, job in enumerate(jobs):
        slices[i % effective_workers].append(job)

    log.info("Scoring %d jobs with %d workers (stagger=%.1fs)...", n, effective_workers, stagger)
    t0 = time.time()

    def _score_slice(slice_jobs, worker_id):
        """Score a slice of jobs. Returns list of result dicts."""
        results = []
        for job in slice_jobs:
            result = score_job(resume_text, job, profile=profile, preferences=preferences)
            result["url"] = job["url"]
            results.append(result)
        return results

    # Launch workers with staggered starts
    from concurrent.futures import ThreadPoolExecutor, as_completed
    all_results: list[dict] = []
    total_errors = 0

    with ThreadPoolExecutor(max_workers=effective_workers) as pool:
        futures = {}
        for i, slice_jobs in enumerate(slices):
            if not slice_jobs:
                continue
            # Stagger start: worker i waits i * stagger seconds
            def _delayed_score(sid=slice_jobs, wid=i):
                time.sleep(wid * stagger)
                return _score_slice(sid, wid)
            fut = pool.submit(_delayed_score)
            futures[fut] = i

        for fut in as_completed(futures):
            worker_id = futures[fut]
            try:
                worker_results = fut.result()
                all_results.extend(worker_results)
                worker_errors = sum(1 for r in worker_results if r["score"] is None)
                total_errors += worker_errors
                log.info("Worker %d: %d jobs scored, %d errors",
                         worker_id, len(worker_results), worker_errors)
            except Exception as e:
                log.error("Worker %d failed: %s", worker_id, e)
                total_errors += len(slices[worker_id])

    # Sort results by original job order for consistent logging
    job_order = {r["url"]: i for i, r in enumerate(all_results)}
    all_results.sort(key=lambda r: job_order.get(r["url"], 999999))

    # Write scores to DB in batches of 50
    now = datetime.now(timezone.utc).isoformat()
    run_id = current_run_id()
    batch_size = 50
    for idx, r in enumerate(all_results):
        if r["score"] is None:
            conn.execute(
                "UPDATE jobs SET score_error = ?, scored_at = ?, scored_run_id = ? WHERE url = ?",
                (r.get("error", "Unknown error"), now, run_id, r["url"]),
            )
        else:
            # Store fit/gap as structured reasoning
            reasoning_parts = []
            if r.get("scoring_method"):
                reasoning_parts.append(f"METHOD: {r['scoring_method']}")
            if r.get("fit"):
                reasoning_parts.append(f"FIT: {r['fit']}")
            if r.get("gap"):
                reasoning_parts.append(f"GAP: {r['gap']}")
            if r.get("keywords"):
                reasoning_parts.append(f"KEYWORDS: {r['keywords']}")
            reasoning_text = "\n".join(reasoning_parts)

            conn.execute(
                """
                UPDATE jobs
                SET fit_score = ?, score_reasoning = ?, fit_summary = ?, gap_summary = ?, keywords = ?,
                    scored_at = ?, scored_run_id = ?
                WHERE url = ?
                """,
                (r["score"], reasoning_text, r.get("fit", ""), r.get("gap", ""), r.get("keywords", ""),
                 now, run_id, r["url"]),
            )

        # Commit every batch_size to avoid losing progress on crash
        if (idx + 1) % batch_size == 0:
            conn.commit()
            log.info("Checkpoint: committed %d/%d scores", idx + 1, len(all_results))

    # Final commit for remaining
    conn.commit()

    elapsed = time.time() - t0
    log.info("Done: %d scored in %.1fs (%.1f jobs/sec)", len(all_results), elapsed, len(all_results) / elapsed if elapsed > 0 else 0)

    # Mark low-scoring jobs inactive. Retention pruning can hard-delete later.
    pruned = conn.execute(
        """
        UPDATE jobs
        SET status = 'low_score', status_reason = ?
        WHERE fit_score IS NOT NULL AND fit_score < ? AND status = 'active'
        """,
        (f"fit_score below min_score {min_score}", min_score),
    ).rowcount
    conn.commit()
    if pruned:
        log.info("Marked %d jobs below score %d as low_score", pruned, min_score)

    # Score distribution (active jobs after lifecycle marking)
    dist = conn.execute("""
        SELECT fit_score, COUNT(*) FROM jobs
        WHERE fit_score IS NOT NULL AND status = 'active'
        GROUP BY fit_score ORDER BY fit_score DESC
    """).fetchall()
    distribution = [(row[0], row[1]) for row in dist]

    return {
        "scored": len(all_results),
        "errors": total_errors,
        "pruned": pruned,
        "elapsed": elapsed,
        "distribution": distribution,
    }
