"""Tests for the HHRMA Bali extractor.

Offline fixtures only. The live board serves /job/<id>/ pages from
job-sitemap.xml. Post sitemaps are the retired theme and must not be fetched.
The extractor fetches once per process, not once per query.
"""

import logging
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobmatch.discovery.smartextract import (  # noqa: E402
    _HHRMA_SITEMAP_INDEX,
    _extract_hhrma_bali,
    _hhrma_job_sitemap_urls,
    _hhrma_parse_post,
)


def _page(title, company, body, *, slug="", area="", deadline="", published="2026-09-24",
          canonical="https://www.hhrmabali.com/job/1/", salary_line=""):
    loc = ""
    if slug and area:
        loc = (
            f'<li class="lk-job__meta-item"><a href="https://www.hhrmabali.com/location/{slug}/">{area}</a></li>'
        )
    salary = f"<p>Gaji {salary_line}</p>" if salary_line else ""
    deadline_html = f"<p>Deadline: {deadline}</p>" if deadline else ""
    return f"""<html><head>
<link rel="canonical" href="{canonical}" />
<meta property="og:title" content="{title}" />
<script type="application/ld+json">{{"datePublished":"{published}T02:42:16+00:00"}}</script>
</head><body>
<article class="lk-job">
<p class="lk-job__company"><a href="https://www.hhrmabali.com/companies/x/">{company}</a></p>
{loc}
<div class="lk-job__description">
<h1 class="lk-job__content-title">{title}</h1>
<p>{body}</p>
{salary}{deadline_html}
</div>
<section class="lk-job__apply"><a href="{canonical}?lkapply=start">Apply now</a></section>
</article></body></html>"""


PA_PAGE = _page(
    "Personal Assistant",
    "Personal Assistant for Investor",
    "Persyaratan: wajib bisa berbahasa Inggris.",
    slug="denpasar",
    area="Denpasar",
    salary_line="Rp 4 – 6 juta++ / bulan",
    canonical="https://www.hhrmabali.com/job/101/",
)
FO_PAGE = _page(
    "Front office",
    "Blue Karma",
    "Guest service at the front desk.",
    slug="seminyak",
    area="Seminyak",
    published="2026-09-22",
    canonical="https://www.hhrmabali.com/job/102/",
)
EXPIRED_PAGE = _page(
    "Reservation staff",
    "Old Hotel",
    "This deadline has passed.",
    slug="ubud",
    area="Ubud",
    deadline="January 2, 2026",
    published="2026-09-25",
    canonical="https://www.hhrmabali.com/job/103/",
)
NOT_A_JOB = "<html><body><h1>About us</h1><p>No vacancy here.</p></body></html>"

INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.hhrmabali.com/job-sitemap.xml</loc></sitemap>
  <sitemap><loc>https://www.hhrmabali.com/post-sitemap4.xml</loc></sitemap>
  <sitemap><loc>https://www.hhrmabali.com/page-sitemap.xml</loc></sitemap>
</sitemapindex>"""

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  <url><loc>https://www.hhrmabali.com/job/100/</loc><image:image><image:loc>https://www.hhrmabali.com/old.jpg</image:loc></image:image><lastmod>2026-04-01T00:00:00+00:00</lastmod></url>
  <url><loc>https://www.hhrmabali.com/job/102/</loc><image:image><image:loc>https://www.hhrmabali.com/fo.jpg</image:loc></image:image><lastmod>2026-09-22T00:00:00+00:00</lastmod></url>
  <url><loc>https://www.hhrmabali.com/job/101/</loc><lastmod>2026-09-24T05:40:18+00:00</lastmod></url>
  <url><loc>https://www.hhrmabali.com/job/103/</loc><lastmod>2026-09-25T00:00:00+00:00</lastmod></url>
  <url><loc>https://www.hhrmabali.com/job/104/</loc><lastmod>2026-09-26T01:00:00+00:00</lastmod></url>
</urlset>"""

PAGES = {
    "https://www.hhrmabali.com/job/101/": PA_PAGE,
    "https://www.hhrmabali.com/job/102/": FO_PAGE,
    "https://www.hhrmabali.com/job/103/": EXPIRED_PAGE,
    "https://www.hhrmabali.com/job/104/": NOT_A_JOB,
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
        if url.endswith("/job-sitemap.xml"):
            return FakeResp(SITEMAP)
        if "post-sitemap" in url:
            raise AssertionError("post sitemaps are the retired theme and must not be fetched")
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


def test_sitemap_index_uses_job_sitemap_and_ignores_posts():
    urls = _hhrma_job_sitemap_urls(INDEX)
    assert urls == ["https://www.hhrmabali.com/job-sitemap.xml"]


def test_parse_post_extracts_title_company_location_salary_and_date():
    import jobmatch.discovery.smartextract as st
    st._hhrma_today = lambda: date(2026, 9, 27)
    job = _hhrma_parse_post(PA_PAGE, "https://www.hhrmabali.com/job/101/", "2026-09-24")
    assert job is not None
    assert job["title"] == "Personal Assistant"
    assert job["company"] == "Personal Assistant for Investor"
    assert job["location"] == "Denpasar, Bali"
    assert job["date_posted"] == "2026-09-24"
    assert job["url"].endswith("/job/101/")
    assert "Persyaratan" in job["description"]
    assert "juta" in job["salary"]
    assert "Apply now" not in job["description"]


def test_parse_drops_expired_deadline_and_non_job_pages():
    import jobmatch.discovery.smartextract as st
    st._hhrma_today = lambda: date(2026, 9, 27)
    assert _hhrma_parse_post(EXPIRED_PAGE, "https://www.hhrmabali.com/job/103/") is None
    assert _hhrma_parse_post(NOT_A_JOB, "https://www.hhrmabali.com/job/104/") is None


def test_foreign_area_is_not_relabelled_bali():
    import jobmatch.discovery.smartextract as st
    from jobmatch.config.locations import location_ok

    st._hhrma_today = lambda: date(2026, 9, 27)
    page = _page(
        "Front office", "Remote Resort", "Front desk work.",
        slug="sumbawa", area="Sumbawa",
        canonical="https://www.hhrmabali.com/job/200/",
    )
    job = _hhrma_parse_post(page, "https://www.hhrmabali.com/job/200/")
    assert job is not None
    assert job["location"] == "Sumbawa"
    assert "bali" not in job["location"].lower()
    accept = ["Bali", "Denpasar", "Canggu", "Ubud", "Seminyak", "Gianyar", "Legian", "Uluwatu"]
    assert location_ok("Sumbawa", accept, [], accept_remote=False) is False
    sidemen = _page(
        "Supervisor", "Numa", "Villa operations.",
        slug="sidemen", area="Sidemen",
        canonical="https://www.hhrmabali.com/job/201/",
    )
    sidemen_job = _hhrma_parse_post(sidemen, "https://www.hhrmabali.com/job/201/")
    assert sidemen_job is not None
    assert sidemen_job["location"] == "Sidemen, Bali"
    assert location_ok(sidemen_job["location"], accept, [], accept_remote=False) is True


def test_missing_location_stays_bali_not_a_foreign_guess():
    import jobmatch.discovery.smartextract as st
    st._hhrma_today = lambda: date(2026, 9, 27)
    page = _page("HR Admin", "Numa Bali", "Staff administration and payroll.")
    job = _hhrma_parse_post(page, "https://www.hhrmabali.com/job/1/")
    assert job is not None
    assert job["location"] == "Bali"


def test_image_between_loc_and_lastmod_still_counts():
    """The live job sitemap puts <image:image> between loc and lastmod."""
    import jobmatch.discovery.smartextract as st
    entries = st._hhrma_sitemap_entries(SITEMAP)
    assert ("https://www.hhrmabali.com/job/100/", date(2026, 4, 1)) in entries
    assert ("https://www.hhrmabali.com/job/102/", date(2026, 9, 22)) in entries


def test_extractor_fetches_once_per_process_not_per_query(monkeypatch):
    calls = []
    _install(monkeypatch, calls)

    queries = ["admin", "front office", "personal assistant", "resepsionis",
               "florist", "reservation", "staf administrasi", "guest service"]
    totals = [_extract_hhrma_bali("HHRMA Bali", "", q) for q in queries]

    page_fetches = [url for url in calls if url in PAGES]
    assert calls.count(_HHRMA_SITEMAP_INDEX) == 1
    assert calls.count("https://www.hhrmabali.com/job-sitemap.xml") == 1
    assert not any("post-sitemap" in url for url in calls)
    assert "https://www.hhrmabali.com/job/100/" not in calls
    assert sorted(page_fetches) == sorted(PAGES)
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
    assert page_fetches == ["https://www.hhrmabali.com/job/104/"]
    assert jobs == []


def test_zero_parsed_pages_warns(monkeypatch, caplog):

    calls = []
    _install(monkeypatch, calls)
    monkeypatch.setenv("JOBMATCH_HHRMA_MAX_PAGES", "1")
    caplog.set_level(logging.WARNING, logger="jobmatch.discovery.smartextract")
    jobs = _extract_hhrma_bali("HHRMA Bali", "", "admin")
    assert jobs == []
    assert any("parsed 0" in rec.message for rec in caplog.records)


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
        conn, [{"url": "https://www.hhrmabali.com/job/102/", "title": "Front office",
                "description": body, "location": "Seminyak, Bali", "date_posted": "2026-09-22"}],
        "HHRMA Bali", "api_extractor", ["Bali", "Seminyak"], [], accept_remote=False,
    )
    assert new == 1
    row = conn.execute(
        "SELECT full_description, detail_scraped_at FROM jobs WHERE url = ?",
        ("https://www.hhrmabali.com/job/102/",)).fetchone()
    assert row[0] == body
    assert row[1] is not None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
