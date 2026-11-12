"""Retention pruning: a dry run must not touch the database."""

from jobmatch import database


def _seed(tmp_path):
    conn = database.init_db(tmp_path / "jobmatch.db")
    conn.executemany(
        "INSERT INTO jobs (url, title, status, discovered_at, scored_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("https://x/old-active", "Old active", "active", "2020-01-01T00:00:00", None),
            ("https://x/old-low", "Old low", "low_score", "2020-01-01T00:00:00", "2020-01-02T00:00:00"),
            ("https://x/new-low", "New low", "low_score", "2999-01-01T00:00:00", "2999-01-02T00:00:00"),
        ],
    )
    conn.commit()
    return conn


def test_dry_run_counts_but_changes_nothing(tmp_path):
    conn = _seed(tmp_path)

    assert database.prune_jobs(days=30, dry_run=True, conn=conn) == 1

    rows = dict(conn.execute("SELECT url, status FROM jobs").fetchall())
    assert rows["https://x/old-active"] == "active", "dry run must not expire active rows"
    assert len(rows) == 3


def test_real_prune_expires_then_deletes_old_rows_only(tmp_path):
    conn = _seed(tmp_path)

    deleted = database.prune_jobs(days=30, dry_run=False, conn=conn)

    rows = dict(conn.execute("SELECT url, status FROM jobs").fetchall())
    assert deleted == 2
    assert rows == {"https://x/new-low": "low_score"}
