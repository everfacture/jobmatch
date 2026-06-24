import os

from jobmatch import pipeline
from jobmatch.pipeline import run_pipeline


def test_run_pipeline_dry_run_requires_no_runtime_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBMATCH_DIR", str(tmp_path))
    monkeypatch.delenv("JOBMATCH_NOTIFY", raising=False)

    result = run_pipeline(["score"], dry_run=True)

    assert result["errors"] == {}
    assert result["stages"] == []
    assert (tmp_path / "jobmatch.db").exists() is False
    assert os.environ.get("JOBMATCH_RUN_ID") is None


def test_run_pipeline_dry_run_accepts_explicit_default_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBMATCH_DIR", str(tmp_path))
    monkeypatch.delenv("JOBMATCH_NOTIFY", raising=False)

    result = run_pipeline(None, dry_run=True)

    assert result["errors"] == {}
    assert [stage["stage"] for stage in result["stages"]] == []


def test_score_limit_is_passed_to_score_stage(monkeypatch):
    captured = {}

    def fake_run_stage(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"status": "ok"}

    monkeypatch.setattr(pipeline, "_run_stage", fake_run_stage)

    result = pipeline._run_sequential(["score"], min_score=7, workers=2, score_limit=25)

    assert result["errors"] == {}
    assert captured["name"] == "score"
    assert captured["kwargs"]["limit"] == 25
    assert captured["kwargs"]["workers"] == 2
