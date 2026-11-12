"""Missing JobSpy values must be stored as NULL, never as the text 'None'/'nan'.

Regression: str(None) wrote the literal 'None' into application_url for every
LinkedIn row with no direct apply link. URL resolution then re-processed every
such row on every enrich run, re-reading sites.yaml each time (~10 minutes a
night on a 60k-row DB with nothing to enrich).
"""

import math

import pandas as pd

from jobmatch.database import init_db
from jobmatch.discovery import jobspy
from jobmatch.enrichment import detail


def _conn(tmp_path):
    return init_db(tmp_path / "t.db")


def test_store_writes_null_for_missing_cells(tmp_path, monkeypatch):
    monkeypatch.setattr(jobspy, "current_run_id", lambda: None)
    conn = _conn(tmp_path)
    df = pd.DataFrame([
        {"job_url": "https://example.com/a", "title": "Front Desk", "job_url_direct": None,
         "company": float("nan"), "location": "Bali", "date_posted": None, "site": "linkedin",
         "description": "x" * 300},
        {"job_url": "https://example.com/b", "title": "Admin", "job_url_direct": "https://apply.example/b",
         "company": "Hotel", "location": float("nan"), "date_posted": "2026-11-01", "site": "indeed",
         "description": None},
    ])

    assert jobspy.store_jobspy_results(conn, df, "test") == (2, 0)

    rows = {r[0]: tuple(r)[1:] for r in conn.execute(
        "SELECT url, application_url, location, date_posted FROM jobs")}
    assert rows["https://example.com/a"] == (None, "Bali", None)
    assert rows["https://example.com/b"] == ("https://apply.example/b", None, "2026-11-01")
    assert conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE company IN ('nan', 'None') OR application_url = 'None'"
    ).fetchone()[0] == 0


def test_cell_treats_placeholders_as_missing():
    row = {"a": None, "b": math.nan, "c": "None", "d": " nan ", "e": "Bali", "f": pd.NaT}
    assert [jobspy._cell(row, k) for k in "abcdef"] == [None, None, None, None, "Bali", None]


def test_resolve_skips_absolute_urls_and_placeholder_apply_links(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    conn.executemany(
        "INSERT INTO jobs (url, site, application_url, status) VALUES (?, ?, ?, 'active')",
        [("https://example.com/1", "linkedin", "None"),
         ("https://example.com/2", "linkedin", "https://apply.example/2"),
         ("https://example.com/3", "linkedin", None)],
    )
    calls = []
    monkeypatch.setattr(detail, "resolve_url", lambda u, s: calls.append(u) or None)

    stats = detail.resolve_all_urls(conn)

    assert calls == []  # nothing relative, so no per-row work at all
    assert stats["already_absolute"] == 3
    assert stats["app_resolved"] == 0


def test_resolve_still_fixes_relative_urls(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    conn.execute("INSERT INTO jobs (url, site, application_url, status) VALUES "
                 "('/jobs/9', 'Board', '/apply/9', 'active')")
    monkeypatch.setattr(detail, "_load_base_urls", lambda: {"Board": "https://board.example"})

    stats = detail.resolve_all_urls(conn)

    assert stats["resolved"] == 1 and stats["app_resolved"] == 1
    assert tuple(conn.execute("SELECT url, application_url FROM jobs").fetchone()) == (
        "https://board.example/jobs/9", "https://board.example/apply/9")
