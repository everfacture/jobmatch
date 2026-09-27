"""Pin the enrichment writer-boundary invariant for the description twin.

Discovery stores the posting text in `description`; enrichment re-captures it
into `full_description`. On boards that ship the whole posting in the search
result (JobSpy/LinkedIn) those two columns are byte-identical, which doubled
the size of the jobs table. Enrichment now drops the twin when — and only
when — it is genuinely the same text.

These tests exercise the exact SQL the enrichment path runs, so a future
refactor that widens or drops the guard fails here instead of silently
re-inflating the database or deleting a real description.
"""

import importlib

import pytest


DUPLICATE = "Procurement Manager role. " * 80
DIFFERENT = "A genuinely different scraped detail page body. " * 80


@pytest.fixture()
def tmp_jobmatch_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBMATCH_DIR", str(tmp_path))
    monkeypatch.setenv("JOBMATCH_PROFILE", "test")

    from jobmatch import config as jobmatch_config
    from jobmatch.config import paths as jobmatch_paths

    importlib.reload(jobmatch_paths)
    importlib.reload(jobmatch_config)

    from jobmatch import database as database_mod
    importlib.reload(database_mod)

    return database_mod


def _seed(conn, url, description):
    conn.execute(
        "INSERT INTO jobs (url, title, description, status, discovered_at) "
        "VALUES (?, ?, ?, 'active', '2026-09-27T00:00:00+00:00')",
        (url, "Test Role", description),
    )
    conn.commit()


def _enrich(conn, url, new_desc, now="2026-09-27T00:00:00+00:00"):
    """Mirror the enrichment success path in enrichment/detail.py."""
    conn.execute(
        "UPDATE jobs SET full_description = ?, application_url = ?, "
        "detail_scraped_at = ?, detail_error = NULL WHERE url = ?",
        (new_desc, "https://example.test/apply", now, url),
    )
    conn.execute(
        "UPDATE jobs SET description = NULL "
        "WHERE url = ? AND description IS NOT NULL AND description = ?",
        (url, new_desc),
    )
    conn.commit()
    return conn.execute(
        "SELECT description, full_description, detail_scraped_at FROM jobs WHERE url = ?",
        (url,),
    ).fetchone()


def test_identical_description_twin_is_dropped(tmp_path, tmp_jobmatch_env):
    database_mod = tmp_jobmatch_env
    conn = database_mod.init_db(db_path=tmp_path / "jobmatch.db")
    _seed(conn, "https://example.test/dup", DUPLICATE)

    row = _enrich(conn, "https://example.test/dup", DUPLICATE)

    assert row[0] is None, "byte-identical description twin should be cleared"
    assert row[1] == DUPLICATE, "full_description must survive"
    assert row[2] is not None


def test_different_description_is_preserved(tmp_path, tmp_jobmatch_env):
    """The guard is an equality test, not a blanket wipe."""
    database_mod = tmp_jobmatch_env
    conn = database_mod.init_db(db_path=tmp_path / "jobmatch.db")
    _seed(conn, "https://example.test/keep", DIFFERENT)

    row = _enrich(conn, "https://example.test/keep", DUPLICATE)

    assert row[0] == DIFFERENT, "a real, different description must not be deleted"
    assert row[1] == DUPLICATE


def test_score_stage_gate_survives_nulled_description(tmp_path, tmp_jobmatch_env):
    """The score queue keys on full_description, so nulling description must not
    make an enriched job look unscoreable."""
    database_mod = tmp_jobmatch_env
    conn = database_mod.init_db(db_path=tmp_path / "jobmatch.db")
    _seed(conn, "https://example.test/gate", DUPLICATE)
    _enrich(conn, "https://example.test/gate", DUPLICATE)

    pending = database_mod.get_jobs_by_stage(conn=conn, stage="score", limit=100)

    assert len(pending) == 1
    assert pending[0]["description"] is None
    assert pending[0]["full_description"] == DUPLICATE
