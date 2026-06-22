import pytest

from jobmatch.llm.client import _build_fallback_chain, _maybe_prepend_no_think, configured_provider_labels


_ENV_KEYS = [
    "JOBMATCH_LLM_BASE_URL",
    "JOBMATCH_LLM_API_KEY",
    "JOBMATCH_LLM_MODEL",
    "JOBMATCH_LLM_EXTRA_BODY",
    "JOBMATCH_FALLBACK_LLM_BASE_URL",
    "JOBMATCH_FALLBACK_LLM_API_KEY",
    "JOBMATCH_FALLBACK_LLM_MODEL",
    "JOBMATCH_FALLBACK_LLM_EXTRA_BODY",
    "LLM_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_EXTRA_BODY",
    "FALLBACK_LLM_URL",
    "FALLBACK_LLM_API_KEY",
    "FALLBACK_LLM_MODEL",
    "FALLBACK_LLM_EXTRA_BODY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
]


def _clear_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_no_provider_config_raises(monkeypatch):
    _clear_env(monkeypatch)

    with pytest.raises(RuntimeError, match="No LLM provider configured"):
        configured_provider_labels()


def test_canonical_jobmatch_provider_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("JOBMATCH_LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("JOBMATCH_LLM_API_KEY", "test-key")
    monkeypatch.setenv("JOBMATCH_LLM_MODEL", "gpt-4o-mini")

    chain = _build_fallback_chain()

    assert configured_provider_labels() == ["primary/gpt-4o-mini"]
    assert chain[0].base_url == "https://api.openai.com/v1"
    assert chain[0].api_key == "test-key"


def test_remote_provider_without_key_is_not_configured(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("JOBMATCH_LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("JOBMATCH_LLM_MODEL", "gpt-4o-mini")

    with pytest.raises(RuntimeError, match="No LLM provider configured"):
        _build_fallback_chain()


def test_local_provider_can_omit_api_key(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("JOBMATCH_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("JOBMATCH_LLM_MODEL", "llama3.1")

    chain = _build_fallback_chain()

    assert chain[0].base_url == "http://localhost:11434/v1"
    assert chain[0].api_key == ""
    assert chain[0].name == "llama3.1"


def test_legacy_llm_env_aliases_still_work(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("LLM_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_API_KEY", "legacy-key")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-4o-mini")

    chain = _build_fallback_chain()

    assert chain[0].base_url == "https://openrouter.ai/api/v1"
    assert chain[0].api_key == "legacy-key"
    assert chain[0].name == "openai/gpt-4o-mini"


def test_provider_specific_openai_key_keeps_old_wizard_users_working(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    chain = _build_fallback_chain()

    assert chain[0].base_url == "https://api.openai.com/v1"
    assert chain[0].api_key == "openai-key"
    assert chain[0].name == "gpt-4o-mini"


def test_canonical_qwen_model_gets_no_think(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("JOBMATCH_LLM_MODEL", "qwen-max")

    messages = [{"role": "user", "content": "score this job"}]

    assert _maybe_prepend_no_think(messages)[0]["content"].startswith("/no_think\n")
