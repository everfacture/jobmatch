"""Scores must be written as they land, not buffered to the end of the run."""

import pytest

from jobmatch import database
from jobmatch.scoring import scorer


@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    path = tmp_path / "jobmatch.db"
    conn = database.init_db(path)
    for n in (1, 2):
        conn.execute(
            "INSERT INTO jobs (url, title, company, full_description, status) VALUES (?, ?, ?, ?, 'active')",
            (f"https://example.test/{n}", f"Role {n}", "Co", "A full description."),
        )
    conn.commit()
    monkeypatch.setattr(scorer, "get_connection", lambda: database.get_connection(path))
    monkeypatch.setattr(
        scorer, "get_jobs_by_stage",
        lambda **kw: [dict(r) for r in database.get_connection(path).execute(
            "SELECT * FROM jobs WHERE status='active' AND fit_score IS NULL ORDER BY url").fetchall()],
    )
    resume = tmp_path / "resume.txt"
    resume.write_text("resume")
    monkeypatch.setattr(scorer, "RESUME_PATH", resume)
    monkeypatch.setattr(scorer, "load_profile", lambda: {})
    monkeypatch.setattr(scorer, "load_preferences", lambda: {})
    return path


def test_scores_survive_a_worker_dying_mid_slice(seeded_db, monkeypatch):
    calls = []

    def fake_score_job(resume_text, job, profile=None, preferences=None):
        calls.append(job["url"])
        if len(calls) == 2:
            raise RuntimeError("provider went away")
        return {"score": 9, "fit": "fits", "gap": "", "keywords": "", "scoring_method": "test"}

    monkeypatch.setattr(scorer, "score_job", fake_score_job)

    scorer.run_scoring(workers=1, stagger=0)

    rows = {
        r[0]: r[1] for r in database.get_connection(seeded_db).execute(
            "SELECT url, fit_score FROM jobs ORDER BY url").fetchall()
    }
    assert rows["https://example.test/1"] == 9, "first result must be on disk before the second call"
    assert rows["https://example.test/2"] is None
