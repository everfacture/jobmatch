"""Tests for the HHRMA Bali extractor.

Offline fixtures only. The live site disallows /wp-json/ and /wp-admin/, and
its HTML /page/N archive repeats the first 12 cards. The extractor must use
the sitemap plus post pages, and must not refetch those inside the per-query
loop.
"""

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobmatch.discovery.smartextract import (  # noqa: E402
    _HHRMA_SITEMAP_INDEX,
    _extract_hhrma_bali,
    _hhrma_parse_post,
    _hhrma_post_sitemap_urls,
)


def _page(title, company, area, body, deadline="October 24, 2026", published="2026-09-24",
          canonical="https://www.hhrmabali.com/personal-assistant-vacancy/", salary_line=""):
    extra = f"<p>Gaji</p><ul><li>{salary_line}</li></ul>" if salary_line else ""
    return f"""<html><head>
<link rel="canonical" href="{canonical}" />
<meta property="article:published_time" content="{published}T05:40:18+00:00" />
</head><body>
<h1 id="job-title"><span>{title}</span></h1>
<a class="company-name" href="https://www.hhrmabali.com/company/x/"><svg></svg>{company}</a>
<div id="location-text"><a href="https://www.hhrmabali.com/location/denpasar/">{area}</a></div>
<div id="job-deadline__text">Deadline: {deadline}</div>
<div id="about-company">Looking for a {title}.</div>
<div id="single-content"><p>{body}</p>{extra}</div>
<div id="how-to-apply"><div id="how-to-apply__instruction"><p>Send a CV.</p></div></div>
</body></html>"""


PA_PAGE = _page(
    "Personal Assistant",
    "Personal Assistant for Investor",
    "Denpasar",
    "Persyaratan: wajib bisa berbahasa Inggris.",
    salary_line="Rp 4 – 6 juta++ / bulan",
)
FO_PAGE = _page(
    "Front office",
    "Blue Karma",
    "Seminyak",
    "Guest service at the front desk.",
    published="2026-09-22",
    canonical="https://www.hhrmabali.com/front-office-role/",
)
EXPIRED_PAGE = _page(
    "Reservation staff",
    "Old Hotel",
    "Ubud",
    "This deadline has passed.",
    deadline="January 2, 2026",
    published="2026-09-25",
    canonical="https://www.hhrmabali.com/expired-role/",
)
NOT_A_JOB = "<html><body><h1>About us</h1><p>No vacancy here.</p></body></html>"

INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.hhrmabali.com/post-sitemap4.xml</loc></sitemap>
  <sitemap><loc>https://www.hhrmabali.com/post-sitemap3.xml</loc></sitemap>
  <sitemap><loc>https://www.hhrmabali.com/page-sitemap.xml</loc></sitemap>
</sitemapindex>"""

SITEMAP4 = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.hhrmabali.com/old-job/</loc><lastmod>2026-04-01T00:00:00+00:00</lastmod></url>
  <url><loc>https://www.hhrmabali.com/front-office-role/</loc><lastmod>2026-09-22T00:00:00+00:00</lastmod></url>
  <url><loc>https://www.hhrmabali.com/personal-assistant-vacancy/</loc><lastmod>2026-09-24T05:40:18+00:00</lastmod></url>
  <url><loc>https://www.hhrmabali.com/expired-role/</loc><lastmod>2026-09-25T00:00:00+00:00</lastmod></url>
  <url><loc>https://www.hhrmabali.com/not-a-job/</loc><lastmod>2026-09-26T01:00:00+00:00</lastmod></url>
</urlset>"""

PAGES = {
    "https://www.hhrmabali.com/personal-assistant-vacancy/": PA_PAGE,
    "https://www.hhrmabali.com/front-office-role/": FO_PAGE,
    "https://www.hhrmabali.com/expired-role/": EXPIRED_PAGE,
    "https://www.hhrmabali.com/not-a-job/": NOT_A_JOB,
}


class FakeResp:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


def _install(monkeypatch, calls):
    import jobmatch.discovery.smartextract as st

    def fake_get(url, **kwargs):
        calls.append(url)
        if url == _HHRMA_SITEMAP_INDEX:
            return FakeResp(INDEX)
        if url.endswith("/post-sitemap4.xml"):
            return FakeResp(SITEMAP4)
        if url.endswith("/post-sitemap3.xml"):
            raise AssertionError("older sitemap must not be fetched once the newest file covers the window")
        if url in PAGES:
            return FakeResp(PAGES[url])
        raise AssertionError(f"unexpected fetch: {url}")

    st._HHRMA_JOB_CACHE = None
    st._HHRMA_REQUESTS = 0
    st._HHRMA_DISABLED_LOGGED = False
    monkeypatch.setattr(st, "_hhrma_today", lambda: date(2026, 9, 27))
    monkeypatch.setattr(st.httpx, "get", fake_get)
    monkeypatch.setenv("HHRMA_BALI_ENABLED", "true")
    monkeypatch.delenv("JOBMATCH_HHRMA_DAYS", raising=False)
    monkeypatch.delenv("JOBMATCH_HHRMA_MAX_PAGES", raising=False)


def test_sitemap_index_prefers_highest_post_sitemap_and_ignores_pages():
    urls = _hhrma_post_sitemap_urls(INDEX)
    assert urls == ["https://www.hhrmabali.com/post-sitemap4.xml",
                    "https://www.hhrmabali.com/post-sitemap3.xml"]


def test_company_h1_uses_body_role_and_drops_script_json():
    import jobmatch.discovery.smartextract as st
    st._hhrma_today = lambda: date(2026, 9, 27)
    page = """<html><head>
<link rel="canonical" href="https://www.hhrmabali.com/sarva-receptionist/" />
<meta property="article:published_time" content="2026-09-23T00:00:00+00:00" />
</head><body>
<h1 id="job-title">Sarva Ubud Villas &amp; Spa</h1>
<div id="location-text"><a href="/location/ubud/">Ubud</a></div>
<div id="job-deadline__text">Deadline: October 24, 2026</div>
<div id="about-company">We are hiring professionals to fill in particular positions.</div>
<div id="single-content">
<h2>Receptionist</h2>
<p>Requirements</p>
<p>Fluent in English.</p>
<script type="application/ld+json">{"@type":"JobPosting","title":"Sarva Ubud Villas & Spa"}</script>
</div>
<div id="how-to-apply"></div>
</body></html>"""
    job = _hhrma_parse_post(page, "https://www.hhrmabali.com/sarva-receptionist/")
    assert job is not None
    assert job["title"] == "Receptionist"
    assert job["company"] == "Sarva Ubud Villas & Spa"
    assert "JobPosting" not in job["description"]
    assert "Fluent in English." in job["description"]


def test_company_h1_skips_boilerplate_heading():
    import jobmatch.discovery.smartextract as st
    st._hhrma_today = lambda: date(2026, 9, 27)
    page = """<html><head>
<link rel="canonical" href="https://www.hhrmabali.com/manara-ar/" />
<meta property="article:published_time" content="2026-09-20T00:00:00+00:00" />
</head><body>
<h1 id="job-title">Manara Management</h1>
<div id="location-text"><a href="/location/denpasar/">Denpasar</a></div>
<div id="job-deadline__text">Deadline: October 20, 2026</div>
<div id="about-company">We are hiring</div>
<div id="single-content">
<h2>We are hiring</h2>
<p>Account Receivable (AR)</p>
<p>Join our growing team and be a part of the journey.</p>
</div>
<div id="how-to-apply"></div>
</body></html>"""
    job = _hhrma_parse_post(page, "https://www.hhrmabali.com/manara-ar/")
    assert job is not None
    assert job["title"] == "Account Receivable (AR)"
    assert job["company"] == "Manara Management"


def test_parse_post_extracts_title_company_location_salary_and_date():
    import jobmatch.discovery.smartextract as st
    st._hhrma_today = lambda: date(2026, 9, 27)
    job = _hhrma_parse_post(PA_PAGE, "https://www.hhrmabali.com/personal-assistant-vacancy/", "2026-09-24")
    assert job is not None
    assert job["title"] == "Personal Assistant"
    assert job["company"] == "Personal Assistant for Investor"
    assert job["location"] == "Denpasar"
    assert job["date_posted"] == "2026-09-24"
    assert job["url"].endswith("/personal-assistant-vacancy/")
    assert "Persyaratan" in job["description"]
    assert "juta" in job["salary"]
    assert "Send a CV." in job["description"]


def test_parse_drops_expired_deadline_and_non_job_pages():
    import jobmatch.discovery.smartextract as st
    st._hhrma_today = lambda: date(2026, 9, 27)
    assert _hhrma_parse_post(EXPIRED_PAGE, "https://www.hhrmabali.com/expired-role/") is None
    assert _hhrma_parse_post(NOT_A_JOB, "https://www.hhrmabali.com/not-a-job/") is None


def test_foreign_area_is_not_relabelled_bali():
    import jobmatch.discovery.smartextract as st
    from jobmatch.config.locations import location_ok

    st._hhrma_today = lambda: date(2026, 9, 27)
    page = _page("Front office", "Remote Resort", "Sumbawa", "Front desk work.",
                 canonical="https://www.hhrmabali.com/sumbawa-front-office/")
    job = _hhrma_parse_post(page, "https://www.hhrmabali.com/sumbawa-front-office/")
    assert job is not None
    assert job["location"] == "Sumbawa"
    assert "bali" not in job["location"].lower()
    accept = ["Bali", "Denpasar", "Canggu", "Ubud", "Seminyak", "Gianyar", "Legian", "Uluwatu"]
    assert location_ok("Sumbawa", accept, [], accept_remote=False) is False
    assert location_ok("Canggu", accept, [], accept_remote=False) is True


def test_extractor_fetches_once_per_process_not_per_query(monkeypatch):
    """Regression: run_smart_extract() calls each extractor once per query.

    A fetch inside that loop would turn a 46-query profile into ~46 copies of
    the same sitemap and page set.
    """
    calls = []
    _install(monkeypatch, calls)

    queries = ["admin", "front office", "personal assistant", "resepsionis",
               "florist", "reservation", "staf administrasi", "guest service"]
    totals = [_extract_hhrma_bali("HHRMA Bali", "", q) for q in queries]

    page_fetches = [url for url in calls if "/post-sitemap" not in url and not url.endswith("sitemap_index.xml")]
    assert calls.count(_HHRMA_SITEMAP_INDEX) == 1
    assert calls.count("https://www.hhrmabali.com/post-sitemap4.xml") == 1
    assert "https://www.hhrmabali.com/post-sitemap3.xml" not in calls
    assert "https://www.hhrmabali.com/old-job/" not in calls
    assert sorted(page_fetches) == sorted(PAGES)
    # two real vacancies survive; expired and non-job do not
    assert {job["title"] for job in totals[0]} == {"Personal Assistant", "Front office"}
    front = next(batch for batch, query in zip(totals, queries) if query == "front office")
    assert [job["title"] for job in front] == ["Front office"]
    florist = next(batch for batch, query in zip(totals, queries) if query == "florist")
    assert florist == []


def test_max_pages_caps_post_fetches(monkeypatch):
    calls = []
    _install(monkeypatch, calls)
    monkeypatch.setenv("JOBMATCH_HHRMA_MAX_PAGES", "1")
    jobs = _extract_hhrma_bali("HHRMA Bali", "", "admin")
    page_fetches = [url for url in calls if url in PAGES]
    assert page_fetches == ["https://www.hhrmabali.com/not-a-job/"]
    assert jobs == []


def test_extractor_respects_disable_switch(monkeypatch):
    import jobmatch.discovery.smartextract as st

    def boom(*args, **kwargs):
        raise AssertionError("must not fetch when disabled")

    st._HHRMA_JOB_CACHE = [{"title": "should not leak"}]
    st._HHRMA_DISABLED_LOGGED = False
    monkeypatch.setenv("HHRMA_BALI_ENABLED", "false")
    monkeypatch.setattr(st.httpx, "get", boom)
    assert _extract_hhrma_bali("HHRMA Bali", "") == []


def test_self_describing_hhrma_is_immediately_scorable(monkeypatch):
    import jobmatch.discovery.smartextract as st

    monkeypatch.setattr(st, "current_run_id", lambda: 1)
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE jobs (
        id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, company TEXT, salary TEXT,
        description TEXT, full_description TEXT, location TEXT, site TEXT, strategy TEXT,
        discovered_at TEXT, discovered_run_id INTEGER, date_posted TEXT,
        detail_scraped_at TEXT, detail_error TEXT)""")
    body = "Front office trainee. Send a CV."
    new, _ = st._store_jobs_filtered(
        conn, [{"url": "https://www.hhrmabali.com/front-office-role/", "title": "Front office",
                "description": body, "location": "Seminyak, Bali, Indonesia", "date_posted": "2026-09-22"}],
        "HHRMA Bali", "api_extractor", ["Bali", "Seminyak"], [], accept_remote=False,
    )
    assert new == 1
    row = conn.execute(
        "SELECT full_description, detail_scraped_at FROM jobs WHERE url = ?",
        ("https://www.hhrmabali.com/front-office-role/",)).fetchone()
    assert row[0] == body
    assert row[1] is not None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
