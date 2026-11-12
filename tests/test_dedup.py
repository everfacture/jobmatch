"""Dedup keeps distinct adverts that only share a title."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobmatch.scoring.dedup import run_dedup  # noqa: E402


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE jobs (
            url TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            description TEXT,
            full_description TEXT,
            application_url TEXT,
            location TEXT,
            status TEXT,
            status_reason TEXT,
            fit_score INTEGER,
            scored_at TEXT
        )
    """)
    return conn


def _add(conn, url, title, company, body, score, scored_at, application_url=None, status="active"):
    conn.execute(
        """INSERT INTO jobs (url, title, company, description, full_description, application_url,
           status, fit_score, scored_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (url, title, company, body, body, application_url, status, score, scored_at),
    )


def _status(conn, url):
    row = conn.execute("SELECT status, status_reason FROM jobs WHERE url = ?", (url,)).fetchone()
    return row["status"], row["status_reason"]


def test_various_positions_with_different_bodies_both_stay():
    conn = _db()
    _add(conn, "https://t.me/carikerja_bali/1", "Various Positions", "",
         "Email: hrd@yolla.com", 9, "2026-09-29T00:00:00")
    _add(conn, "https://t.me/carikerja_bali/2", "Various Positions", "",
         "Email: recruitment@thenest.com", 9, "2026-10-06T00:00:00")
    result = run_dedup(conn)
    assert result["duplicates_removed"] == 0
    assert _status(conn, "https://t.me/carikerja_bali/1") == ("active", None)
    assert _status(conn, "https://t.me/carikerja_bali/2") == ("active", None)


def test_same_title_no_company_collapses_only_when_body_matches():
    conn = _db()
    _add(conn, "https://t.me/carikerja_bali/1", "GUEST SERVICE AGENT", "",
         "Send CV to hrd@hotel-a.com", 9, "2026-10-08T00:00:00")
    _add(conn, "https://t.me/carikerja_bali/2", "GUEST SERVICE AGENT", "",
         "Send CV to hrd@hotel-a.com", 8, "2026-10-12T00:00:00")
    _add(conn, "https://t.me/carikerja_bali/3", "GUEST SERVICE AGENT", "",
         "Send CV to hrd@hotel-b.com", 9, "2026-10-12T01:00:00")
    result = run_dedup(conn)
    assert result["by_title_only"] == 1
    assert _status(conn, "https://t.me/carikerja_bali/1")[0] == "active"
    assert _status(conn, "https://t.me/carikerja_bali/2") == ("duplicate", "duplicate title")
    assert _status(conn, "https://t.me/carikerja_bali/3")[0] == "active"


def test_same_title_and_company_with_different_body_stays():
    conn = _db()
    _add(conn, "https://www.linkedin.com/jobs/view/1", "Sales Admin", "Travel + Leisure Co.",
         "First advert, front desk and reservations.", 9, "2026-10-06T00:00:00")
    _add(conn, "https://www.linkedin.com/jobs/view/2", "Sales Admin", "Travel + Leisure Co.",
         "New advert twelve days later, different duties.", 10, "2026-10-12T00:00:00")
    result = run_dedup(conn)
    assert result["duplicates_removed"] == 0
    assert _status(conn, "https://www.linkedin.com/jobs/view/1")[0] == "active"
    assert _status(conn, "https://www.linkedin.com/jobs/view/2")[0] == "active"


def test_same_title_and_company_same_body_collapses():
    conn = _db()
    _add(conn, "https://www.linkedin.com/jobs/view/1", "Sales Admin", "Travel + Leisure Co.",
         "Same posting text.", 8, "2026-10-06T00:00:00")
    _add(conn, "https://www.linkedin.com/jobs/view/2", "Sales Admin", "Travel + Leisure Co.",
         "Same posting text.", 10, "2026-10-12T00:00:00")
    run_dedup(conn)
    assert _status(conn, "https://www.linkedin.com/jobs/view/2")[0] == "active"
    assert _status(conn, "https://www.linkedin.com/jobs/view/1") == ("duplicate", "duplicate title+company")


def test_same_url_collapses_even_when_titles_differ():
    conn = _db()
    _add(conn, "https://www.linkedin.com/jobs/view/9", "Receptionist", "Soma",
         "Clinic front desk.", 8, "2026-10-17T00:00:00",
         application_url="https://apply.example/9")
    _add(conn, "https://www.linkedin.com/jobs/view/10", "Clinic Receptionist", "Soma",
         "Different scrape of the same apply link.", 9, "2026-10-18T00:00:00",
         application_url="https://apply.example/9")
    result = run_dedup(conn)
    assert result["by_url"] == 1
    assert _status(conn, "https://www.linkedin.com/jobs/view/10")[0] == "active"
    assert _status(conn, "https://www.linkedin.com/jobs/view/9") == ("duplicate", "duplicate url")


def test_literal_none_application_url_does_not_glue_rows():
    conn = _db()
    _add(conn, "https://www.linkedin.com/jobs/view/1", "Admin", "Hotel A",
         "Body A", 9, "2026-10-01T00:00:00", application_url="None")
    _add(conn, "https://www.linkedin.com/jobs/view/2", "Admin", "Hotel B",
         "Body B", 9, "2026-10-02T00:00:00", application_url="None")
    result = run_dedup(conn)
    assert result["by_url"] == 0
    assert result["duplicates_removed"] == 0


def test_score_below_threshold_is_left_alone():
    conn = _db()
    _add(conn, "https://t.me/x/1", "Various Positions", "", "Email a", 6, "2026-10-01T00:00:00")
    _add(conn, "https://t.me/x/2", "Various Positions", "", "Email a", 6, "2026-10-02T00:00:00")
    result = run_dedup(conn)
    assert result["duplicates_removed"] == 0
    assert result["total_remaining"] == 0
