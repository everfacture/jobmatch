"""exclude_titles must apply to every discovery path, not just JobSpy.

Regression: the filter lived in jobspy.py and was only applied on the JobSpy
path, so smart-extract sources (Telegram, Dealls, HHRMA) stored rows the
profile had explicitly excluded -- e.g. "SITE SUPERVISOR" landed in the finda
DB despite `supervisor` being in exclude_titles.
"""

import sqlite3

import pytest

from jobmatch.discovery.smartextract import _store_jobs_filtered
from jobmatch.discovery.titlefilter import normalise_phrase, title_excluded


def test_normalise_phrase_collapses_separators():
    assert normalise_phrase("Site  Supervisor!!") == "site supervisor"
    assert normalise_phrase("entry-level") == normalise_phrase("entry level")
    assert normalise_phrase(None) == ""


def test_title_excluded_is_whole_word():
    ex = ["supervisor", "intern"]
    assert title_excluded("SITE SUPERVISOR", ex) is True
    assert title_excluded("Front Office Supervisor", ex) is True
    # whole-word: "intern" must not match "internal" or "international"
    assert title_excluded("Internal Audit Officer", ex) is False
    assert title_excluded("Administrative Officer", ex) is False
    assert title_excluded("", ex) is False
    assert title_excluded("Anything", None) is False


def _bare_conn() -> sqlite3.Connection:
    """Minimal but schema-accurate: _store_jobs_filtered inserts these columns."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE jobs (
        id INTEGER PRIMARY KEY, url TEXT, title TEXT, company TEXT, salary TEXT,
        description TEXT, location TEXT, site TEXT, strategy TEXT,
        discovered_at TEXT, discovered_run_id INTEGER)""")
    return conn


def test_store_applies_exclude_titles_on_smart_extract_path():
    conn = _bare_conn()
    jobs = [
        {"url": "u1", "title": "SITE SUPERVISOR", "location": "Bali, Indonesia"},
        {"url": "u2", "title": "Front Office Intern", "location": "Bali, Indonesia"},
        {"url": "u3", "title": "Front Office Guest Service", "location": "Bali, Indonesia"},
    ]
    new, existing = _store_jobs_filtered(
        conn, jobs, "Telegram Bali", "api_extractor",
        ["Bali"], [], accept_remote=False,
        exclude_titles=["supervisor", "intern"],
    )
    assert new == 1, "only the non-excluded job should be stored"
    assert existing == 0
    rows = conn.execute("SELECT title FROM jobs").fetchall()
    assert [r[0] for r in rows] == ["Front Office Guest Service"]


def test_store_without_exclude_titles_keeps_everything():
    """Backwards compatible: no rules configured means no filtering."""
    conn = _bare_conn()
    jobs = [{"url": "u1", "title": "SITE SUPERVISOR", "location": "Bali, Indonesia"}]
    new, _ = _store_jobs_filtered(
        conn, jobs, "Telegram Bali", "api_extractor",
        ["Bali"], [], accept_remote=False,
    )
    assert new == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
