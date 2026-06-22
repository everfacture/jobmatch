"""Workday ATS direct API scraper: searches employer career portals.

Scrapes Workday-powered career sites (DBS Bank, Visa) via the undocumented
CXS JSON API. Zero LLM, zero browser -- pure HTTP.

Employer registry is loaded from config/employers.yaml.
"""

import json
import logging
import re
import sqlite3
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

from jobmatch import config
from jobmatch.config import CONFIG_DIR, location_ok, load_location_accept_reject
from jobmatch.database import get_connection, init_db, current_run_id

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# -- Employer registry from YAML --------------------------------------------

def load_employers() -> dict:
    """Load Workday employer registry from config/employers.yaml."""
    path = CONFIG_DIR / "employers.yaml"
    if not path.exists():
        log.warning("employers.yaml not found at %s", path)
        return {}
    data = __import__("yaml").safe_load(path.read_text(encoding="utf-8"))
    return data.get("employers", {})


# -- HTML stripper -----------------------------------------------------------

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        elif tag in ("p", "div", "li", "tr"):
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"[^\S\n]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def strip_html(html: str) -> str:
    if not html:
        return ""
    s = _HTMLStripper()
    s.feed(html)
    return s.get_text()


# -- Workday CXS API ---------------------------------------------------------

def workday_search(employer: dict, search_text: str, limit: int = 20, offset: int = 0) -> dict:
    """Search jobs via Workday CXS API."""
    url = f"{employer['base_url']}/wday/cxs/{employer['tenant']}/{employer['site_id']}/jobs"
    payload = json.dumps({
        "appliedFacets": {}, "limit": limit, "offset": offset, "searchText": search_text,
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def workday_detail(employer: dict, external_path: str) -> dict:
    """Fetch full job detail via Workday CXS API."""
    url = f"{employer['base_url']}/wday/cxs/{employer['tenant']}/{employer['site_id']}{external_path}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


# -- Search + paginate -------------------------------------------------------

def search_employer(
    employer: dict,
    search_text: str,
    accept_locs: list[str],
    reject_locs: list[str],
) -> list[dict]:
    """Search one employer, paginate through results, filter by location."""
    name = employer["name"]
    log.info("%s: searching \"%s\"...", name, search_text)

    all_jobs: list[dict] = []
    offset = 0
    page_size = 20
    total = None

    while True:
        try:
            data = workday_search(employer, search_text, limit=page_size, offset=offset)
        except Exception as e:
            log.error("%s: API error at offset %d: %s", name, offset, e)
            break

        if total is None:
            total = data.get("total", 0)
            log.info("%s: %d total results", name, total)

        postings = data.get("jobPostings", [])
        if not postings:
            break

        for j in postings:
            loc = j.get("locationsText", "")
            if not location_ok(loc, accept_locs, reject_locs):
                continue
            all_jobs.append({
                "title": j.get("title", ""),
                "location": loc,
                "posted": j.get("postedOn", ""),
                "external_path": j.get("externalPath", ""),
                "employer_name": name,
            })

        offset += page_size
        if offset >= total or offset >= 500:  # cap at 500 results
            break

    log.info("%s: %d jobs found", name, len(all_jobs))
    return all_jobs


# -- Fetch details -----------------------------------------------------------

def fetch_details(employer: dict, jobs: list[dict]) -> None:
    """Fetch full description + apply URL for each job (mutates in place)."""
    for job in jobs:
        try:
            detail = workday_detail(employer, job["external_path"])
            info = detail.get("jobPostingInfo", {})
            job["full_description"] = strip_html(info.get("jobDescription", ""))
            job["apply_url"] = info.get("externalUrl", "")
        except Exception as e:
            job["full_description"] = ""
            job["apply_url"] = ""
            job["detail_error"] = str(e)


# -- DB storage --------------------------------------------------------------

def store_results(conn: sqlite3.Connection, jobs: list[dict], employer_name: str) -> tuple[int, int]:
    """Store corporate jobs in DB. Returns (new, existing)."""
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0
    run_id = current_run_id()

    for job in jobs:
        url = job.get("apply_url", "")
        if not url:
            continue
        desc = job.get("full_description", "")
        full_description = desc if len(desc) > 200 else None
        try:
            conn.execute(
                "INSERT INTO jobs (url, title, salary, description, location, site, strategy, "
                "discovered_at, full_description, application_url, detail_scraped_at, detail_error, discovered_run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (url, job.get("title"), None, desc[:500], job.get("location"),
                 employer_name, "workday_api", now, full_description, url,
                 now if full_description else None, job.get("detail_error"), run_id),
            )
            new += 1
        except sqlite3.IntegrityError:
            existing += 1

    conn.commit()
    return new, existing


# -- Public entry point ------------------------------------------------------

def run_workday_discovery(employers: dict | None = None, workers: int = 1) -> dict:
    """Main entry point for Workday-based corporate job discovery."""
    if employers is None:
        employers = load_employers()
    if not employers:
        log.warning("No employers configured. Create config/employers.yaml.")
        return {"found": 0, "new": 0, "existing": 0, "queries": 0}

    search_cfg = config.load_search_config()
    queries = [q["query"] for q in search_cfg.get("queries", []) if q.get("tier", 99) <= 2]
    if not queries:
        log.warning("No search queries configured.")
        return {"found": 0, "new": 0, "existing": 0, "queries": 0}

    accept_locs, reject_locs = load_location_accept_reject(search_cfg)
    init_db()

    grand_new = 0
    grand_existing = 0
    grand_found = 0

    for i, query in enumerate(queries, 1):
        log.info("Query %d/%d: \"%s\"", i, len(queries), query)
        for emp_key, emp in employers.items():
            jobs = search_employer(emp, query, accept_locs, reject_locs)
            grand_found += len(jobs)
            if not jobs:
                continue
            fetch_details(emp, jobs)
            conn = get_connection()
            new, existing = store_results(conn, jobs, emp["name"])
            grand_new += new
            grand_existing += existing
            log.info("%s: %d new, %d dupes", emp["name"], new, existing)

    log.info("Workday crawl done: %d found, %d new, %d existing across %d queries x %d employers",
             grand_found, grand_new, grand_existing, len(queries), len(employers))

    return {"found": grand_found, "new": grand_new, "existing": grand_existing, "queries": len(queries)}
