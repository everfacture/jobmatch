import importlib

import pytest

from jobmatch.llm.client import LLMClient


def test_token_priced_models_keep_dollar_estimate():
    cost = LLMClient._estimate_cost("deepseek-v4-flash", 1_000_000, 1_000_000)

    assert cost == pytest.approx(0.42)
    assert LLMClient._request_count("deepseek-v4-flash") is None


def test_jatevo_gpt55_is_request_counted_not_token_priced():
    assert LLMClient._estimate_cost("gpt-5.5", 399_133, 33_703) is None
    assert LLMClient._request_count("gpt-5.5") == 1
    assert LLMClient._request_count(" GPT-5.5 ") == 1


def test_record_llm_usage_persists_request_count(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBMATCH_DIR", str(tmp_path))
    monkeypatch.setenv("JOBMATCH_PROFILE", "test")
    monkeypatch.setenv("JOBMATCH_RUN_ID", "123")

    from jobmatch import config as jobmatch_config
    from jobmatch.config import paths as jobmatch_paths

    importlib.reload(jobmatch_paths)
    importlib.reload(jobmatch_config)

    from jobmatch import database as database_mod

    importlib.reload(database_mod)
    db_path = tmp_path / "jobmatch.db"
    conn = database_mod.init_db(db_path=db_path)

    database_mod.record_llm_usage(
        stage="score",
        provider_label="primary/gpt-5.5",
        model="gpt-5.5",
        prompt_tokens=399_133,
        completion_tokens=33_703,
        total_tokens=432_836,
        estimated_cost_usd=None,
        request_count=1,
        elapsed_ms=15000,
        conn=conn,
    )

    row = conn.execute(
        "SELECT pipeline_run_id, model, request_count, estimated_cost_usd FROM llm_usage"
    ).fetchone()
    assert dict(row) == {
        "pipeline_run_id": 123,
        "model": "gpt-5.5",
        "request_count": 1,
        "estimated_cost_usd": None,
    }
