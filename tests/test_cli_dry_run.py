from typer.testing import CliRunner

from jobmatch.cli import app


def test_cli_run_dry_run_needs_no_llm_or_runtime_files(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBMATCH_DIR", str(tmp_path))
    monkeypatch.delenv("JOBMATCH_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("JOBMATCH_LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("JOBMATCH_NOTIFY", raising=False)

    result = CliRunner().invoke(app, ["run", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output
    assert "discover -> enrich -> score -> dedup" in result.output
    assert (tmp_path / "jobmatch.db").exists() is False
