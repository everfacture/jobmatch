import pytest


@pytest.fixture()
def tmp_jobmatch_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBMATCH_DIR", str(tmp_path))
    monkeypatch.setenv("JOBMATCH_PROFILE", "test")

    from jobmatch import config as jobmatch_config
    from jobmatch.config import paths as jobmatch_paths

    import importlib
    importlib.reload(jobmatch_paths)
    importlib.reload(jobmatch_config)

    from jobmatch import database as database_mod
    importlib.reload(database_mod)

    return database_mod


def test_init_db_and_get_jobs_by_stage(tmp_path, tmp_jobmatch_env):
    database_mod = tmp_jobmatch_env
    db_path = tmp_path / "jobmatch.db"
    conn = database_mod.init_db(db_path=db_path)
    database_mod.close_connection(db_path=db_path)

    database_mod.store_jobs(
        database_mod.get_connection(db_path),
        [
            {"url": "https://example.test/a", "title": "Backend Engineer", "description": "Desc A"},
            {"url": "https://example.test/b", "title": "Backend Engineer", "description": "Desc B", "full_description": "Full B"},
        ],
        site="example",
        strategy="test",
    )

    conn = database_mod.get_connection(db_path)
    conn.execute(
        "UPDATE jobs SET detail_scraped_at = datetime('now') WHERE url = ?",
        ("https://example.test/b",),
    )
    conn.execute(
        "UPDATE jobs SET full_description = ? WHERE url = ?",
        ("Full B", "https://example.test/b"),
    )
    conn.commit()

    jobs = database_mod.get_jobs_by_stage(conn=conn, stage="discovered")
    assert len(jobs) == 2
    assert jobs[0]["status"] == "active"

    jobs_with_desc = database_mod.get_jobs_by_stage(conn=conn, stage="enriched")
    assert len(jobs_with_desc) == 1
    assert jobs_with_desc[0]["url"] == "https://example.test/b"


def test_count_pending_raises_for_unknown_stage(tmp_path, tmp_jobmatch_env):
    database_mod = tmp_jobmatch_env
    db_path = tmp_path / "jobmatch.db"
    conn = database_mod.init_db(db_path=db_path)

    with pytest.raises(ValueError, match="Unknown stage: banana"):
        database_mod.count_pending("banana", conn=conn)


def test_get_jobs_by_stage_raises_for_unknown_stage(tmp_path, tmp_jobmatch_env):
    database_mod = tmp_jobmatch_env
    db_path = tmp_path / "jobmatch.db"
    conn = database_mod.init_db(db_path=db_path)

    with pytest.raises(ValueError, match="Unknown stage: banana"):
        database_mod.get_jobs_by_stage(conn=conn, stage="banana")


def test_stats_reflect_seeded_rows(tmp_path, tmp_jobmatch_env):
    database_mod = tmp_jobmatch_env
    db_path = tmp_path / "jobmatch.db"
    conn = database_mod.init_db(db_path=db_path)

    database_mod.store_jobs(
        conn,
        [
            {"url": "https://example.test/a", "title": "A"},
            {"url": "https://example.test/b", "title": "B", "full_description": "Full"},
        ],
        site="example",
        strategy="test",
    )

    stats = database_mod.get_stats(conn)
    assert stats["total"] == 2
    assert stats["pending_detail"] == 2
    assert stats["scored"] == 0
