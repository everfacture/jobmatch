"""pipeline_runs bookkeeping and discovery kill-switches."""

import importlib

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBMATCH_DIR", str(tmp_path))
    monkeypatch.setenv("JOBMATCH_PROFILE", "test")
    from jobmatch import config as jobmatch_config
    from jobmatch.config import paths as jobmatch_paths
    importlib.reload(jobmatch_paths)
    importlib.reload(jobmatch_config)
    from jobmatch import database as database_mod
    importlib.reload(database_mod)
    database_mod.init_db(tmp_path / "jobmatch.db")
    return database_mod


def test_new_run_closes_orphaned_running_rows(db, tmp_path):
    conn = db.get_connection(tmp_path / "jobmatch.db")
    killed = db.start_pipeline_run(["discover"], 7, 1, conn=conn)
    # Simulate a process that died mid-run: no finish_pipeline_run call.
    fresh = db.start_pipeline_run(["score"], 7, 1, conn=conn)

    rows = {r[0]: r for r in conn.execute(
        "SELECT id, status, finished_at, error FROM pipeline_runs"
    ).fetchall()}
    assert rows[killed][1] == "aborted"
    assert rows[killed][2] is not None
    assert "exited" in rows[killed][3]
    assert rows[fresh][1] == "running"

    db.finish_pipeline_run(fresh, "ok", {}, conn=conn)
    assert conn.execute(
        "SELECT status FROM pipeline_runs WHERE id = ?", (fresh,)
    ).fetchone()[0] == "ok"


def test_workday_disabled_by_env_makes_no_requests(monkeypatch):
    from jobmatch.discovery import workday

    monkeypatch.setenv("WORKDAY_ENABLED", "false")

    def boom(*args, **kwargs):
        raise AssertionError("Workday must not load config or hit the network when disabled")

    monkeypatch.setattr(workday, "load_employers", boom)
    monkeypatch.setattr(workday.config, "load_search_config", boom)
    assert workday.run_workday_discovery() == {"found": 0, "new": 0, "existing": 0, "queries": 0}
