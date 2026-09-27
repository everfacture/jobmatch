"""Smart extraction: discovers jobs via known APIs (Apify actors + Dealls REST).

The old AI-powered generic scraping (Playwright API interception, LLM strategy
selection, CSS selector generation) was stripped — it produced near-zero yield
across 40 sites while burning minutes of Playwright + LLM time per run.

What remains: targeted extractors for sites where we know the API.
"""

import html
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

import httpx
import yaml

from jobmatch import config
from jobmatch.config import CONFIG_DIR, location_ok, load_location_accept_reject
from jobmatch.config.locations import location_accepts_remote
from jobmatch.database import init_db, current_run_id
from jobmatch.discovery.titlefilter import title_excluded

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

                job = {
                    "title": j.get("role", ""),
                    "url": job_url,
                    "company": company_info.get("name", ""),
                    "location": city_info.get("name", ""),
                    "salary": _format_dealls_salary(salary_range),
                    "description": j.get("description", ""),
                }
                if _dealls_query_relevant(job, query):
                    jobs.append(job)

            total_pages = data.get("data", {}).get("totalPages", 1)
            if page >= total_pages:
                break
            page += 1
        except Exception as e:
            log.warning("Dealls API error on page %d: %s", page, e)
            break

    log.info("Dealls: extracted %d relevant jobs for '%s'", len(jobs), query or "")
    return jobs


def _search_terms(text: str) -> set[str]:
    """Token set for loose relevance checks against noisy search APIs."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    stop = {
        "and", "or", "the", "for", "di", "dan", "yang", "staff", "staf", "admin",
        "assistant", "asisten", "junior", "senior", "indonesia", "kerja", "lowongan",
    }
    return {w for w in words if len(w) >= 3 and w not in stop}


_DEALLS_GENERIC_QUERY_TERMS = {
    "admin", "assistant", "asisten", "staff", "staf", "customer", "service",
    "product", "production", "creative", "event", "operation", "operations",
    "development", "designer", "brand", "activation", "order", "store",
}


def _dealls_query_relevant(job: dict, query: str | None) -> bool:
    """Keep only niche matches because Dealls' endpoint may ignore its keyword."""
    terms = _search_terms(query or "")
    # The endpoint has returned the same global/recent list for unrelated
    # searches such as florist, hampers and visual merchandising. A match on
    # generic title fragments like "customer service" is not evidence of a
    # creative-lane match; require a specialist anchor from the query.
    anchors = terms - _DEALLS_GENERIC_QUERY_TERMS
    if not anchors:
        return False
    text = " ".join(str(job.get(k) or "") for k in ("title", "company", "description"))
    haystack = _search_terms(text)
    if anchors & haystack:
        return True

    # Keep a tiny synonym bridge for Indonesian/English pairs used in configs.
    synonyms = {
        "hampers": {"gift", "souvenir", "packaging", "packing", "hadiah", "parsel"},
        "gift": {"hampers", "souvenir", "packaging", "packing", "hadiah", "parsel"},
        "packaging": {"packing", "hampers", "gift", "box", "pita", "hadiah"},
        "packing": {"packaging", "hampers", "gift", "box", "pita", "hadiah"},
        "florist": {"flower", "bunga", "buket", "bouquet"},
        "bunga": {"florist", "flower", "buket", "bouquet"},
        "buket": {"florist", "flower", "bunga", "bouquet"},
        "display": {"visual", "merchandiser", "merchandising", "posm", "booth"},
        "merchandiser": {"display", "visual", "merchandising"},
        "souvenir": {"gift", "hampers", "merchandise", "packaging", "packing"},
    }
    expanded = set(anchors)
    for term in anchors:
        expanded.update(synonyms.get(term, set()))
    return bool(expanded & haystack)


@register_extractor("Glints Indonesia")
def _extract_glints(name: str, url: str, query: str | None = None) -> list[dict]:
    """Glints Indonesia job board via Apify actor. ~$2/1000 results."""
    if not _apify_enabled():
        return []

    api_key = os.environ.get("APIFY_API_KEY", "").strip()
    if not api_key or api_key == "***":
        return []

    if not query:
        return []

    global _APIFY_DISABLED_FOR_RUN
    if _APIFY_DISABLED_FOR_RUN:
        return []

    try:
        resp = httpx.post(
            "https://api.apify.com/v2/acts/crawlerbros~glints-scraper/run-sync-get-dataset-items",
            params={"token": api_key},
            json={"mode": "search", "searchQuery": query, "country": "ID", "maxItems": 25},
            timeout=180,
        )
        if resp.status_code != 201:
            body = resp.text[:500].lower()
            if any(w in body for w in ("credit", "quota", "insufficient", "402", "429")):
                log.warning("Glints: Apify credit-limited; disabling Apify for this run")
                _APIFY_DISABLED_FOR_RUN = True
            else:
                log.warning("Glints API returned %s", resp.status_code)
            return []

        items = resp.json()
        if not isinstance(items, list):
            return []

        jobs = []
        for item in items:
            # crawlerbros actor uses camelCase fields
            title = item.get("title") or item.get("jobTitle") or ""
            company = item.get("companyName") or item.get("company_name") or ""
            url = item.get("url") or item.get("jobUrl") or item.get("platform_url") or ""
            location_str = ""
            loc = item.get("location")
            if isinstance(loc, dict):
                location_str = loc.get("raw") or loc.get("locality") or loc.get("city") or ""
            elif isinstance(loc, str):
                location_str = loc
            desc = item.get("description") or item.get("jobDescription") or ""
            salary = ""
            if item.get("salary_minimum") or item.get("salaryMin"):
                sal_min = item.get("salary_minimum") or item.get("salaryMin")
                sal_max = item.get("salary_maximum") or item.get("salaryMax")
                salary = _fmt_idr(sal_min)
                if sal_max:
                    salary += f"-{_fmt_idr(sal_max)}"
                salary += f" {item.get('salary_currency', item.get('salaryCurrency', 'IDR'))}"

            jobs.append({
                "title": title,
                "url": url,
                "company": company,
                "location": location_str,
                "salary": salary,
                "description": desc,
            })

        log.info("Glints: extracted %d jobs for '%s'", len(jobs), query)
        return jobs
    except Exception as e:
        log.warning("Glints error: %s", e)
        return []


@register_extractor("Telegram Bali")
def _extract_telegram_bali(name: str, url: str, query: str | None = None) -> list[dict]:
    """Public Telegram channel mirror (t.me/s/<channel>) for Bali job channels.

    Telegram's web preview serves the last ~20 public posts of a channel with no
    login and no API key, which makes it the cheapest high-freshness source
    available to us. Verified 2026-09-27: @carikerja_bali carries 17-19 posts
    per 24h, @loker_bali a similar volume, and the posts are Bahasa Indonesia
    role/salary/intake notices that the Indonesian-language queries in
    searches.yaml were written to match.

    Note on ``?before=``: Telegram ignores it for these channels and returns the
    same page, so this is a today-only feed with no backfill. That is fine for a
    freshness-first pipeline but must not be mistaken for full history.
    """
    if os.environ.get("TELEGRAM_BALI_ENABLED", "true").strip().lower() in {"0", "false", "no", "off", "disabled"}:
        log.info("Telegram Bali disabled: TELEGRAM_BALI_ENABLED=false")
        return []

    channels = [c.strip().lstrip("@") for c in
                os.environ.get("JOBMATCH_TELEGRAM_CHANNELS", _TELEGRAM_BALI_CHANNELS_DEFAULT).split(",")
                if c.strip()]
    if not channels:
        return []

    # run_smart_extract() calls every extractor once per search query, and each
    # call takes 3-60s against Telegram from this VPS. Fetching inside that loop
    # meant ~92 requests for a 46-query profile to re-read the same ~20 posts.
    # Cache per process so a full run costs one fetch per channel.
    posts_by_channel: dict[str, list[tuple[str, str, str]]] = {}
    for channel in channels:
        if channel in _TELEGRAM_POST_CACHE:
            posts_by_channel[channel] = _TELEGRAM_POST_CACHE[channel]
            continue
        try:
            resp = httpx.get(f"https://t.me/s/{channel}",
                             headers={"User-Agent": UA}, timeout=30, follow_redirects=True)
            if resp.status_code != 200:
                log.warning("Telegram %s returned %s", channel, resp.status_code)
                posts_by_channel[channel] = []
                continue
        except Exception as e:
            log.warning("Telegram %s fetch error: %s", channel, e)
            posts_by_channel[channel] = []
            continue
        parsed = _telegram_parse_posts(resp.text)
        _TELEGRAM_POST_CACHE[channel] = parsed
        posts_by_channel[channel] = parsed

    jobs: list[dict] = []
    seen: set[str] = set()
    for channel, posts in posts_by_channel.items():
        for post_id, dt, text in posts:
            # The channel post is the only stable identity we get; without a link
            # in the post body we synthesise the public permalink.
            link = f"https://t.me/{channel}/{post_id}"
            if link in seen:
                continue

            title = _telegram_job_title(text)
            if not title:
                continue
            if query and not _telegram_query_relevant(title, text, query):
                continue

            seen.add(link)
            jobs.append({
                "title": title,
                "url": link,
                "company": "",
                "location": "Bali, Indonesia",
                "salary": "",
                "description": text,
                "date_posted": dt,
            })

    log.info("Telegram Bali: extracted %d jobs from %d channels", len(jobs), len(channels))
    return jobs


# channel -> parsed posts, reused across the per-query extractor loop.
_TELEGRAM_POST_CACHE: dict[str, list[tuple[str, str, str]]] = {}


_TELEGRAM_BALI_CHANNELS_DEFAULT = "carikerja_bali,loker_bali"

# Sources whose discovery payload already contains the complete posting text, so
# a detail-page fetch adds nothing. A Telegram channel post, and an HHRMA Bali
# job page, IS the whole advert. Opening it again in a browser yields no extra
# text and just times out (~60s per job). The writer promotes the body to
# `full_description` and stamps `detail_scraped_at`, which keeps these rows out
# of the pending detail scrape (`detail_scraped_at IS NULL`) and makes them
# immediately scorable.
SELF_DESCRIBING_SITES = {"Telegram Bali", "HHRMA Bali"}

_TG_POST_RE = re.compile(
    r'data-post="([^"]+?)".*?<time datetime="([^"]+?)".*?'
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
    re.S,
)
_TG_LINK_RE = re.compile(r'href="(https?://[^"]+)"')
_TG_TITLE_PREFIX_RE = re.compile(
    r"^\s*(?:lowongan\s+kerja\s+)?(?:di\s*)?(?:[*_#\s]*)"
    r"(?:lowongan|info loker|rekrutmen|we're hiring|urgent|sekarang|service|svc)\b[^:!\n]{0,40}[:!\-–]\s*",
    re.I,
)


def _telegram_strip_html(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    # Telegram escapes numeric entities (&#33;), not just named ones, so decode
    # properly instead of hand-rolling a named-entity table.
    text = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    # Telegram posts are emoji-heavy and space-padded; collapse but keep newlines
    # so multi-line role lists stay readable.
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


def _telegram_parse_posts(page_html: str) -> list[tuple[str, str, str]]:
    """Return (post_id, iso_date, text) for each message on a t.me/s/ page."""
    out: list[tuple[str, str, str]] = []
    for post_ref, dt, fragment in _TG_POST_RE.findall(page_html):
        post_id = post_ref.rsplit("/", 1)[-1]
        text = _telegram_strip_html(fragment)
        if text:
            out.append((post_id, dt[:10], text))
    return out


def _telegram_job_title(text: str) -> str:
    """Best-effort role title from a free-form Indonesian job post.

    These posts are unstructured and the channels also carry chatter ("kak mau
    kirim loker ya") and bare reposts of LinkedIn URLs, neither of which is a
    vacancy. Return "" for those so the caller drops the post.
    """
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if len(line) < 4:
            continue
        # Skip pure emoji/decor lines.
        if re.fullmatch(r"[\W_]+", line, re.UNICODE):
            continue
        # A bare link is a repost of someone else's listing, not a vacancy we can
        # score; without a role we would store the URL as the title.
        if re.fullmatch(r"https?://\S+", line):
            continue
        line = _TG_TITLE_PREFIX_RE.sub("", line).strip(" *_#-–—:")
        if len(line) < 4:
            continue
        # Short conversational lines that are clearly not a role title.
        if _TG_CHATTER_RE.match(line):
            continue
        return line[:120]
    return ""


# Conversational filler that shows up in these channels alongside real posts.
_TG_CHATTER_RE = re.compile(
    r"^(kak|hai|halo|hello|hi|selamat|pagi|siang|sore|malam|terima kasih|makasih|"
    r"please|help|info|share|cek|lihat|ikut|join|follow|comment|"
    r"anyone|ada yang|yang bisa|bisa bantu|tolong)\b",
    re.I,
)


_TELEGRAM_GENERIC_TERMS = {
    "lowongan", "kerja", "job", "jobs", "info", "kami", "mencari", "looking",
    "staff", "staf", "admin", "assistant", "asisten", "indonesia", "bali",
    "yang", "dan", "untuk", "dengan", "di", "the", "for", "a",
}


def _telegram_query_relevant(title: str, body: str, query: str) -> bool:
    """Require a non-generic anchor from the query to appear in the post.

    Same reasoning as the Dealls filter: these channels mix jobs with ads and
    chatter, so a generic hit like "admin" is not evidence of a lane match.
    """
    terms = _search_terms(query or "")
    anchors = terms - _TELEGRAM_GENERIC_TERMS
    if not anchors:
        return True
    haystack = _search_terms(f"{title} {body}")
    return bool(anchors & haystack)


# HHRMA Bali. robots.txt (User-agent: *) disallows /wp-json/, /?rest_route=,
# /wp-admin/ (including admin-ajax.php) and /feed/. A 200 from the REST API is
# not permission. HTML /page/N and category archives repeat the same 12
# preloaded cards, so they are not a backfill route. The sitemap is linked from
# robots.txt, and each post page is ordinary HTML that already contains the
# full advert. Fetch those once per process, then filter in memory.
_HHRMA_SITEMAP_INDEX = "https://www.hhrmabali.com/sitemap_index.xml"
_HHRMA_JOB_CACHE: list[dict] | None = None
_HHRMA_REQUESTS = 0
_HHRMA_DISABLED_LOGGED = False

_HHRMA_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_HHRMA_LOC_RE = re.compile(r"<(?:\w+:)?loc>([^<]+)</(?:\w+:)?loc>")
_HHRMA_ENTRY_RE = re.compile(
    r"<(?:\w+:)?loc>([^<]+)</(?:\w+:)?loc>\s*<(?:\w+:)?lastmod>([^<]+)</(?:\w+:)?lastmod>",
    re.S,
)


def _hhrma_enabled() -> bool:
    return os.environ.get("HHRMA_BALI_ENABLED", "true").strip().lower() not in {
        "0", "false", "no", "off", "disabled",
    }


def _hhrma_days() -> int:
    raw = os.environ.get("JOBMATCH_HHRMA_DAYS", "7").strip() or "7"
    try:
        days = int(raw)
    except ValueError:
        days = 7
    return max(1, min(days, 31))


def _hhrma_max_pages() -> int:
    raw = os.environ.get("JOBMATCH_HHRMA_MAX_PAGES", "60").strip() or "60"
    try:
        cap = int(raw)
    except ValueError:
        cap = 60
    return max(1, min(cap, 80))


def _hhrma_today() -> date:
    return datetime.now(timezone.utc).date()


def _hhrma_get(url: str):
    """One counted GET. Callers must not invoke this inside the per-query loop."""
    global _HHRMA_REQUESTS
    _HHRMA_REQUESTS += 1
    return httpx.get(
        url,
        headers={"User-Agent": UA, "Accept": "text/html,application/xml,text/xml,*/*"},
        timeout=30,
        follow_redirects=True,
    )


def _hhrma_text(fragment: str) -> str:
    fragment = re.sub(r"<script\b[^>]*>.*?</script>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<style\b[^>]*>.*?</style>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"</(?:p|div|li|h\d|ul|ol)>", "\n", fragment, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


def _hhrma_section(page: str, element_id: str, end_ids: tuple[str, ...]) -> str:
    start = re.search(rf'id="{element_id}"[^>]*>', page)
    if not start:
        return ""
    rest = page[start.end():]
    end_at = len(rest)
    for eid in end_ids:
        marker = re.search(rf'<div id="{eid}"', rest)
        if marker:
            end_at = min(end_at, marker.start())
    return rest[:end_at][:12000]


_HHRMA_BOILER_RE = re.compile(
    r"^(?:we are hiring(?: professionals\b.*)?|apply now|requirements|qualifications|"
    r"about the role|how to apply|career guides|key responsibilit\w*|"
    r"your responsibilities|responsibilities|persyaratan|lingkup pekerjaan|"
    r"gaji|join our|please send|min \d)\b",
    re.I,
)


def _hhrma_usable_role(text: str) -> bool:
    text = text.strip(" *-–—:")
    if not (3 <= len(text) <= 80) or text.endswith("."):
        return False
    if _HHRMA_BOILER_RE.match(text):
        return False
    return re.search(r"[A-Za-z]", text) is not None


def _hhrma_role_from_page(page: str) -> str:
    """Role line when the h1 is the employer, not the job.

    Some HHRMA posts put the company in `#job-title` and the role in the body
    (`Receptionist`, `E-commerce`, `Account Receivable (AR)`).
    """
    fragments = (
        _hhrma_section(page, "single-content", ("how-to-apply",)),
        _hhrma_section(page, "about-company", ("single-content", "how-to-apply")),
    )
    for fragment in fragments:
        for heading in re.findall(r"<h[2-4][^>]*>(.*?)</h[2-4]>", fragment, re.S | re.I):
            text = _hhrma_text(heading)
            if _hhrma_usable_role(text):
                return text[:120]
        for line in _hhrma_text(fragment).split("\n"):
            if _hhrma_usable_role(line):
                return line[:120]
    return ""


def _hhrma_parse_deadline(text: str) -> date | None:
    match = re.search(r"Deadline:\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text)
    if not match:
        return None
    month = _HHRMA_MONTHS.get(match.group(1).lower())
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(2)))
    except ValueError:
        return None


def _hhrma_salary(text: str) -> str:
    match = re.search(r"Rp\s*[\d.]+\s*[–\-]\s*[\d.]+\s*juta[^\n]*", text, re.I)
    if not match:
        match = re.search(r"Rp\s*[\d.]+\s*(?:juta|jt|ribu)[^\n]*", text, re.I)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(0)).strip()


def _hhrma_post_sitemap_urls(index_xml: str) -> list[str]:
    locs = [loc.strip() for loc in _HHRMA_LOC_RE.findall(index_xml)]
    posts = [loc for loc in locs if re.search(r"/post-sitemap\d*\.xml$", loc)]

    def number(url: str) -> int:
        match = re.search(r"post-sitemap(\d*)\.xml", url)
        raw = match.group(1) if match else ""
        return int(raw) if raw else 1

    # Highest file first. Yoast's index lastmod is not trustworthy: post-sitemap.xml
    # was stamped 2026-09-25 while its newest URL was 2024-10-25.
    return sorted(set(posts), key=number, reverse=True)


def _hhrma_sitemap_entries(xml: str) -> list[tuple[str, date]]:
    out: list[tuple[str, date]] = []
    for loc, lastmod in _HHRMA_ENTRY_RE.findall(xml):
        try:
            posted = date.fromisoformat(lastmod.strip()[:10])
        except ValueError:
            continue
        out.append((loc.strip(), posted))
    return out


def _hhrma_parse_post(page: str, source_url: str, lastmod: str = "") -> dict | None:
    """Parse one HHRMA job page. Return None for non-jobs and expired deadlines."""
    title_match = re.search(r'<h1 id="job-title"[^>]*>(.*?)</h1>', page, re.S)
    if not title_match:
        return None
    title = _hhrma_text(title_match.group(1))[:120]
    if len(title) < 2:
        return None

    deadline = _hhrma_parse_deadline(page)
    if deadline is not None and deadline < _hhrma_today():
        return None

    company_match = re.search(r'<a class="company-name"[^>]*>(.*?)</a>', page, re.S)
    company = _hhrma_text(company_match.group(1))[:120] if company_match else ""
    if not company:
        # These posts put the employer in the h1 and the role in the body.
        role = _hhrma_role_from_page(page)
        if role and role.lower() != title.lower():
            company = title
            title = role

    loc_match = re.search(r'id="location-text".*?<a[^>]*>([^<]+)</a>', page, re.S)
    area = html.unescape(loc_match.group(1)).strip() if loc_match else ""
    # Do not stamp "Bali" onto a foreign area. "Canggu" already matches the
    # profile accept list. "Sumbawa" must stay "Sumbawa" so the gate can reject it.
    location = area or "Bali, Indonesia"

    about = _hhrma_text(_hhrma_section(page, "about-company", ("single-content", "how-to-apply")))
    body = _hhrma_text(_hhrma_section(page, "single-content", ("how-to-apply",)))
    apply = _hhrma_text(_hhrma_section(page, "how-to-apply__instruction", ("apply-form",)))
    description = "\n".join(part for part in (about, body, apply) if part).strip()
    if not description:
        return None

    published = re.search(r'article:published_time" content="(\d{4}-\d{2}-\d{2})', page)
    canonical = re.search(r'<link rel="canonical" href="([^"]+)"', page)
    return {
        "title": title,
        "url": canonical.group(1) if canonical else source_url,
        "company": company,
        "location": location,
        "salary": _hhrma_salary(description),
        "description": description,
        "date_posted": published.group(1) if published else (lastmod[:10] or None),
    }


def _hhrma_fetch_jobs() -> list[dict]:
    window_start = _hhrma_today() - timedelta(days=_hhrma_days())
    index = _hhrma_get(_HHRMA_SITEMAP_INDEX)
    if index.status_code != 200:
        log.warning("HHRMA sitemap index returned %s", index.status_code)
        return []

    selected: list[tuple[str, date]] = []
    for sitemap_url in _hhrma_post_sitemap_urls(index.text):
        resp = _hhrma_get(sitemap_url)
        if resp.status_code != 200:
            log.warning("HHRMA sitemap %s returned %s", sitemap_url, resp.status_code)
            continue
        entries = _hhrma_sitemap_entries(resp.text)
        if not entries:
            continue
        oldest = min(posted for _, posted in entries)
        for url, posted in entries:
            if posted >= window_start:
                selected.append((url, posted))
        # This file still covers dates before the window, so older sitemaps cannot
        # add fresher URLs. Stop. Index lastmod must not be used for this decision.
        if oldest < window_start:
            break

    selected.sort(key=lambda item: item[1], reverse=True)
    selected = selected[:_hhrma_max_pages()]

    jobs: list[dict] = []
    seen: set[str] = set()
    for url, posted in selected:
        if url in seen:
            continue
        seen.add(url)
        try:
            resp = _hhrma_get(url)
        except Exception as exc:
            log.warning("HHRMA %s fetch error: %s", url, exc)
            continue
        if resp.status_code != 200:
            log.warning("HHRMA %s returned %s", url, resp.status_code)
            continue
        job = _hhrma_parse_post(resp.text, url, posted.isoformat())
        if job:
            jobs.append(job)

    log.info(
        "HHRMA Bali: cached %d jobs from %d pages (%d http)",
        len(jobs), len(selected), _HHRMA_REQUESTS,
    )
    return jobs


def _hhrma_cached_jobs() -> list[dict]:
    """Fetch once per process. A failure is cached too, so a dead site is not retried per query."""
    global _HHRMA_JOB_CACHE
    if _HHRMA_JOB_CACHE is not None:
        return _HHRMA_JOB_CACHE
    try:
        jobs = _hhrma_fetch_jobs()
    except Exception as exc:
        log.warning("HHRMA Bali fetch failed: %s", exc)
        jobs = []
    _HHRMA_JOB_CACHE = jobs
    return jobs


@register_extractor("HHRMA Bali")
def _extract_hhrma_bali(name: str, url: str, query: str | None = None) -> list[dict]:
    """HHRMA Bali hospitality board, via the robots-allowed sitemap and post pages.

    The public listing is WordPress. The REST API and the Ajax "load more"
    endpoint are disallowed, and the HTML archive does not paginate. Recent
    post pages are the advert. Cached per process because run_smart_extract()
    calls this once per query.
    """
    global _HHRMA_DISABLED_LOGGED
    if not _hhrma_enabled():
        if not _HHRMA_DISABLED_LOGGED:
            log.info("HHRMA Bali disabled: HHRMA_BALI_ENABLED=false")
            _HHRMA_DISABLED_LOGGED = True
        return []

    jobs = _hhrma_cached_jobs()
    if query:
        jobs = [
            job for job in jobs
            if _telegram_query_relevant(job.get("title") or "", job.get("description") or "", query)
        ]
    log.info("HHRMA Bali: %d jobs for %r", len(jobs), query or "")
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
    *,
    accept_remote: bool = True,
    exclude_titles: list[str] | None = None,
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
        # Apply the profile's exclude_titles here too. This filter used to run
        # only on the JobSpy path, so smart-extract sources stored rows the
        # profile had explicitly excluded (e.g. "SITE SUPERVISOR").
        if title_excluded(job.get("title"), exclude_titles):
            filtered += 1
            continue
        if not location_ok(job.get("location"), accept_locs, reject_locs, accept_remote=accept_remote):
            filtered += 1
            continue
        try:
            # Some sources (Telegram, Workday) report a posting date. Persist it
            # when the schema has the column: it is the freshness signal, and
            # without it staleness cannot be judged downstream. Guarded because
            # a bare jobs table (older DBs, minimal test fixtures) may lack it.
            cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
            self_describing = site in SELF_DESCRIBING_SITES
            # Scoring reads full_description, and the pending detail scrape is
            # selected on detail_scraped_at IS NULL. For a self-describing
            # source the post body IS the posting, so promote it now and mark
            # the row as already scraped. Otherwise the job waits on a browser
            # fetch of a permalink that carries no extra text, then times out
            # and never becomes scorable.
            if "date_posted" in cols and self_describing and {"full_description", "detail_scraped_at"} <= cols:
                conn.execute(
                    "INSERT INTO jobs (url, title, company, salary, description, full_description, "
                    "location, site, strategy, discovered_at, discovered_run_id, date_posted, detail_scraped_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (url, job.get("title"), job.get("company"), job.get("salary"), job.get("description"),
                     job.get("description"), job.get("location"), site, strategy, now, run_id,
                     job.get("date_posted") or None, now),
                )
            elif "date_posted" in cols:
                conn.execute(
                    "INSERT INTO jobs (url, title, company, salary, description, location, site, "
                    "strategy, discovered_at, discovered_run_id, date_posted) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (url, job.get("title"), job.get("company"), job.get("salary"), job.get("description"),
                     job.get("location"), site, strategy, now, run_id, job.get("date_posted") or None),
                )
            else:
                conn.execute(
                    "INSERT INTO jobs (url, title, company, salary, description, location, site, "
                    "strategy, discovered_at, discovered_run_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (url, job.get("title"), job.get("company"), job.get("salary"), job.get("description"),
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
    search_cfg = config.load_search_config()
    if queries is None:
        queries = [q["query"] for q in search_cfg.get("queries", []) if q.get("tier", 99) <= 2]

    accept_locs, reject_locs = load_location_accept_reject(search_cfg)
    accept_remote = location_accepts_remote(search_cfg)
    exclude_titles = search_cfg.get("exclude_titles") or []
    conn = init_db()

    # Respect JOBMATCH_SMART_EXTRACT to limit which extractors run.
    # Without it, all registered extractors fire — expensive when only one region matters.
    allowed_raw = os.environ.get("JOBMATCH_SMART_EXTRACT", "").strip()
    if allowed_raw:
        allowed = {e.strip() for e in allowed_raw.split(",")}
        extractors = {k: v for k, v in CUSTOM_EXTRACTORS.items() if k in allowed}
    else:
        extractors = CUSTOM_EXTRACTORS

    log.info("Smart extract: %d queries x %d extractors", len(queries), len(extractors))

    total_new = 0
    total_existing = 0
    total_errors = 0
    t0 = time.time()

    for extractor_name, extractor_func in extractors.items():
        for query in queries:
            try:
                jobs = extractor_func(extractor_name, "", query)
                if jobs:
                    new, existing = _store_jobs_filtered(
                        conn, jobs, extractor_name, "api_extractor",
                        accept_locs, reject_locs,
                        accept_remote=accept_remote,
                        exclude_titles=exclude_titles,
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
