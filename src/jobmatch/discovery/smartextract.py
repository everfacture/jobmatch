"""Smart extraction: discovers jobs via known APIs (Apify actors + Dealls REST).

The old AI-powered generic scraping (Playwright API interception, LLM strategy
selection, CSS selector generation) was stripped — it produced near-zero yield
across 40 sites while burning minutes of Playwright + LLM time per run.

What remains: targeted extractors for sites where we know the API.
"""

import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

import httpx
import yaml

from jobmatch import config
from jobmatch.config import CONFIG_DIR, location_ok, load_location_accept_reject
from jobmatch.database import init_db, current_run_id

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Fix Windows encoding
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# -- Custom API extractors ---------------------------------------------------

CUSTOM_EXTRACTORS: dict[str, callable] = {}


def register_extractor(name: str):
    def decorator(func: callable):
        CUSTOM_EXTRACTORS[name] = func
        return func
    return decorator


@register_extractor("Dealls")
def _extract_dealls(name: str, url: str, query: str | None = None) -> list[dict]:
    """Dealls Indonesian job board — REST API, no auth needed."""
    if os.environ.get("DEALLS_ENABLED", "true").strip().lower() in {"0", "false", "no", "off", "disabled"}:
        log.info("Dealls disabled: DEALLS_ENABLED=false")
        return []

    if not query:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        query = qs.get("keyword", [""])[0]

    jobs: list[dict] = []
    page = 1
    max_pages = 5

    while page <= max_pages:
        try:
            resp = httpx.get(
                "https://api.sejutacita.id/v1/explore-job/job",
                params={"keyword": query, "page": page, "per_page": 50},
                headers={"Accept": "application/json", "User-Agent": UA},
                timeout=30,
            )
            if resp.status_code != 200:
                log.warning("Dealls API returned %s", resp.status_code)
                break

            data = resp.json()
            docs = data.get("data", {}).get("docs", [])
            if not docs:
                break

            for j in docs:
                company_info = j.get("company") or {}
                city_info = j.get("city") or {}
                salary_range = j.get("salaryRange", {})
                slug = j.get("slug", "")
                company_slug = company_info.get("slug", "")
                job_url = f"https://dealls.com/jobs/{company_slug}/{slug}" if slug and company_slug else ""

                jobs.append({
                    "title": j.get("role", ""),
                    "url": job_url,
                    "company": company_info.get("name", ""),
                    "location": city_info.get("name", ""),
                    "salary": _format_dealls_salary(salary_range),
                    "description": j.get("description", ""),
                })

            total_pages = data.get("data", {}).get("totalPages", 1)
            if page >= total_pages:
                break
            page += 1
        except Exception as e:
            log.warning("Dealls API error on page %d: %s", page, e)
            break

    log.info("Dealls: extracted %d jobs for '%s'", len(jobs), query or "")
    return jobs


@register_extractor("Naukri Gulf")
def _extract_naukrigulf(name: str, url: str, query: str | None = None) -> list[dict]:
    """Naukri Gulf via Apify actor."""
    if not query:
        log.warning("NaukriGulf: no search query provided — skipping")
        return []
    items = _apify_run("easyapi~naukrigulf-jobs-scraper", {"searchTerms": [query], "maxResults": 50}, "NaukriGulf")
    return [j for j in (_normalize_apify(item) for item in items) if j is not None]


@register_extractor("Bayt")
def _extract_bayt(name: str, url: str, query: str | None = None) -> list[dict]:
    """Bayt.com via Apify actor."""
    if not query:
        return []
    items = _apify_run("easyapi~bayt-jobs-scraper", {"searchTerms": [query], "maxResults": 50}, "Bayt")
    return [j for j in (_normalize_apify(item) for item in items) if j is not None]


@register_extractor("GulfTalent")
def _extract_gulftalent(name: str, url: str, query: str | None = None) -> list[dict]:
    """GulfTalent.com via Apify actor."""
    if not query:
        return []
    items = _apify_run("shahidirfan~gulftalent-job-scraper", {"searchTerms": [query], "maxResults": 50}, "GulfTalent")
    return [j for j in (_normalize_apify(item) for item in items) if j is not None]


@register_extractor("JobStreet Indonesia")
def _extract_jobstreet_id(name: str, url: str, query: str | None = None) -> list[dict]:
    """JobStreet Indonesia via Apify actor."""
    if not query:
        return []
    items = _apify_run("shahidirfan~jobstreet-scraper", {"searchTerms": [query], "maxResults": 50}, "JobStreet Indonesia")
    return [j for j in (_normalize_apify(item) for item in items) if j is not None]


def _normalize_apify(item: dict) -> dict | None:
    """Normalize an Apify result to standard job dict."""
    title = item.get("title", "") or item.get("jobTitle", "")
    if not title:
        return None
    return {
        "title": title,
        "url": item.get("url", "") or item.get("jobUrl", "") or "",
        "company": item.get("company", "") or item.get("companyName", ""),
        "location": item.get("location", "") or item.get("jobLocation", ""),
        "salary": item.get("salary", "") or item.get("salaryInfo", ""),
        "description": item.get("description", "") or item.get("jobDescription", ""),
    }


_APIFY_CONFIG_WARNED = False
_APIFY_DISABLED_FOR_RUN = False


def _apify_enabled() -> bool:
    raw = os.environ.get("APIFY_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def _apify_run(actor_id: str, payload: dict, label: str) -> list[dict]:
    """Run an Apify actor and fetch results."""
    global _APIFY_CONFIG_WARNED, _APIFY_DISABLED_FOR_RUN

    if _APIFY_DISABLED_FOR_RUN:
        return []

    if not _apify_enabled():
        if not _APIFY_CONFIG_WARNED:
            log.warning("Apify-backed sources disabled: APIFY_ENABLED=false")
            _APIFY_CONFIG_WARNED = True
        return []

    api_key = os.environ.get("APIFY_API_KEY", "").strip()
    if not api_key or api_key == "***":
        if not _APIFY_CONFIG_WARNED:
            log.warning("Apify-backed sources disabled: APIFY_API_KEY is missing or still set to placeholder '***'")
            _APIFY_CONFIG_WARNED = True
        return []

    url = f"https://api.apify.com/v2/acts/{actor_id}/runs?token={api_key}&waitForFinish=120"
    try:
        resp = httpx.post(url, json=payload, timeout=130)
        if resp.status_code != 201:
            body = resp.text[:500]
            body_l = body.lower()
            if resp.status_code in (402, 429) or any(word in body_l for word in ("credit", "quota", "insufficient")):
                log.warning("%s: Apify unavailable/credit-limited (%s); disabling Apify sources for this run", label, resp.status_code)
                _APIFY_DISABLED_FOR_RUN = True
            else:
                log.warning("%s: Apify API returned %s: %s", label, resp.status_code, body[:200])
            return []

        data = resp.json()
        run_id = data.get("data", {}).get("id")
        if not run_id:
            log.warning("%s: no run ID in response", label)
            return []

        dataset_url = f"https://api.apify.com/v2/acts/{actor_id}/runs/{run_id}/dataset?token={api_key}&format=json"
        results_resp = httpx.get(dataset_url, timeout=30)
        if results_resp.status_code != 200:
            body = results_resp.text[:500]
            if results_resp.status_code in (402, 429) or any(word in body.lower() for word in ("credit", "quota", "insufficient")):
                log.warning("%s: Apify dataset unavailable/credit-limited (%s); disabling Apify sources for this run", label, results_resp.status_code)
                _APIFY_DISABLED_FOR_RUN = True
            else:
                log.warning("%s: dataset fetch returned %s", label, results_resp.status_code)
            return []

        items = results_resp.json()
        if not isinstance(items, list):
            items = list(items.values()) if isinstance(items, dict) else []

        log.info("%s (Apify): got %d results", label, len(items))
        return items
    except Exception as e:
        log.warning("%s Apify error: %s", label, e)
        return []


def _format_dealls_salary(salary_range: dict) -> str:
    if not salary_range:
        return ""
    min_sal = salary_range.get("start") or salary_range.get("min")
    max_sal = salary_range.get("end") or salary_range.get("max")
    currency = salary_range.get("currency", "IDR")
    if min_sal and max_sal:
        return f"{_fmt_idr(min_sal)}-{_fmt_idr(max_sal)} {currency}"
    if min_sal:
        return f"{_fmt_idr(min_sal)}+ {currency}"
    return ""


def _fmt_idr(amount: int | float | str) -> str:
    try:
        n = int(float(amount))
    except (ValueError, TypeError):
        return str(amount)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.0f}M"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f}jt"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


# -- Site configuration from YAML --------------------------------------------

def load_sites() -> list[dict]:
    """Load scraping target sites from config/sites.yaml."""
    path = CONFIG_DIR / "sites.yaml"
    if not path.exists():
        log.warning("sites.yaml not found at %s", path)
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("sites", [])


def _store_jobs_filtered(
    conn: sqlite3.Connection,
    jobs: list[dict],
    site: str,
    strategy: str,
    accept_locs: list[str],
    reject_locs: list[str],
) -> tuple[int, int]:
    """Store jobs with location filtering. Returns (new, existing)."""
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0
    filtered = 0
    run_id = current_run_id()

    for job in jobs:
        url = job.get("url")
        if not url:
            continue
        if not location_ok(job.get("location"), accept_locs, reject_locs):
            filtered += 1
            continue
        try:
            conn.execute(
                "INSERT INTO jobs (url, title, salary, description, location, site, strategy, discovered_at, discovered_run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (url, job.get("title"), job.get("salary"), job.get("description"),
                 job.get("location"), site, strategy, now, run_id),
            )
            new += 1
        except sqlite3.IntegrityError:
            existing += 1

    if filtered:
        log.info("Filtered %d jobs (wrong location)", filtered)
    conn.commit()
    return new, existing


# -- Main entry point --------------------------------------------------------

def run_smart_extract(queries: list[str] | None = None, workers: int = 1) -> dict:
    """Run custom API extractors for all registered sites.

    Iterates through CUSTOM_EXTRACTORS, runs each against the search queries,
    stores results with location filtering.

    Args:
        queries: Override the search queries. If None, loads from config.
        workers: Ignored (kept for API compatibility).

    Returns:
        Dict with stats: new, existing, total, errors, queries.
    """
    if queries is None:
        search_cfg = config.load_search_config()
        queries = [q["query"] for q in search_cfg.get("queries", []) if q.get("tier", 99) <= 2]

    accept_locs, reject_locs = load_location_accept_reject()
    conn = init_db()
    log.info("Smart extract: %d queries x %d extractors", len(queries), len(CUSTOM_EXTRACTORS))

    total_new = 0
    total_existing = 0
    total_errors = 0
    t0 = time.time()

    for extractor_name, extractor_func in CUSTOM_EXTRACTORS.items():
        for query in queries:
            try:
                jobs = extractor_func(extractor_name, "", query)
                if jobs:
                    new, existing = _store_jobs_filtered(
                        conn, jobs, extractor_name, "api_extractor",
                        accept_locs, reject_locs,
                    )
                    total_new += new
                    total_existing += existing
                    log.info("%s '%s': +%d new, %d dupes", extractor_name, query, new, existing)
            except Exception as e:
                total_errors += 1
                log.warning("%s '%s' failed: %s", extractor_name, query, e)

    elapsed = time.time() - t0
    log.info("Smart extract done in %.1fs: %d new, %d dupes, %d errors",
             elapsed, total_new, total_existing, total_errors)

    return {
        "new": total_new,
        "existing": total_existing,
        "errors": total_errors,
        "queries": len(queries),
    }
