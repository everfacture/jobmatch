from jobmatch.llm.codex_local import detect_local_codex
from jobmatch.llm.providers import SIMPLE_PROVIDERS, render_llm_env
from jobmatch.view import should_open_dashboard
from jobmatch.wizard.init import build_simple_profile


def test_detect_local_codex_login_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    auth = tmp_path / ".codex" / "auth.json"
    auth.parent.mkdir()
    auth.write_text("{}", encoding="utf-8")

    found = detect_local_codex(home=tmp_path, which=lambda _name: None)

    assert found.detected is True
    assert found.auth_present is True
    assert found.cli_path is None
    assert found.status_label() == "login file"


def test_detect_local_codex_cli_only(tmp_path):
    found = detect_local_codex(home=tmp_path, which=lambda _name: "/usr/bin/codex")

    assert found.detected is True
    assert found.auth_present is False
    assert found.cli_path == "/usr/bin/codex"
    assert found.status_label() == "CLI on PATH"


def test_detect_local_codex_missing(tmp_path):
    found = detect_local_codex(home=tmp_path, which=lambda _name: None)

    assert found.detected is False
    assert found.status_label() == "not found"


def test_detect_local_codex_does_not_read_auth_contents(tmp_path):
    monkeypatch_home = tmp_path
    auth = monkeypatch_home / ".codex" / "auth.json"
    auth.parent.mkdir()
    auth.write_text('{"access_token": "secret-token-do-not-leak"}', encoding="utf-8")

    found = detect_local_codex(home=monkeypatch_home, which=lambda _name: None)

    assert found.auth_present is True
    assert "secret-token-do-not-leak" not in found.status_label()
    assert "secret-token-do-not-leak" not in str(found.auth_path)


def test_render_llm_env_uses_canonical_names_and_console_notifier():
    text = render_llm_env(
        base_url="https://api.deepseek.com/v1/",
        api_key="sk-test",
        model="deepseek-chat",
    )

    assert "JOBMATCH_LLM_BASE_URL=https://api.deepseek.com/v1" in text
    assert "JOBMATCH_LLM_API_KEY=sk-test" in text
    assert "JOBMATCH_LLM_MODEL=deepseek-chat" in text
    assert "JOBMATCH_NOTIFIER=console" in text


def test_simple_providers_are_the_four_civilian_options():
    assert tuple(SIMPLE_PROVIDERS) == ("deepseek", "openai", "openrouter", "groq")


def test_build_simple_profile_has_scoring_fields():
    profile = build_simple_profile(
        full_name="Ada Lovelace",
        email="ada@example.com",
        city="London",
        country="UK",
        target_role="Software Engineer",
    )

    assert profile["personal"]["full_name"] == "Ada Lovelace"
    assert profile["personal"]["preferred_name"] == "Ada"
    assert profile["experience"]["target_role"] == "Software Engineer"
    assert profile["skills_boundary"]["programming_languages"] == []


def test_should_open_dashboard_tty_default_and_overrides(monkeypatch):
    monkeypatch.delenv("JOBMATCH_OPEN_DASHBOARD", raising=False)
    assert should_open_dashboard(isatty=True) is True
    assert should_open_dashboard(isatty=False) is False

    monkeypatch.setenv("JOBMATCH_OPEN_DASHBOARD", "0")
    assert should_open_dashboard(isatty=True) is False

    monkeypatch.setenv("JOBMATCH_OPEN_DASHBOARD", "1")
    assert should_open_dashboard(isatty=False) is True


def test_apply_simple_setup_writes_runtime_files(tmp_path, monkeypatch):
    from jobmatch import simple_setup

    monkeypatch.setattr(simple_setup, "ensure_dirs", lambda: None)
    monkeypatch.setattr(simple_setup, "RESUME_PATH", tmp_path / "resume.txt")
    monkeypatch.setattr(simple_setup, "RESUME_PDF_PATH", tmp_path / "resume.pdf")
    monkeypatch.setattr(simple_setup, "PROFILE_PATH", tmp_path / "profile.json")
    monkeypatch.setattr(simple_setup, "SEARCH_CONFIG_PATH", tmp_path / "searches.yaml")
    monkeypatch.setattr(simple_setup, "PREFERENCES_PATH", tmp_path / "preferences.yaml")
    monkeypatch.setattr(simple_setup, "ENV_PATH", tmp_path / ".env")

    simple_setup.apply_simple_setup(
        full_name="Ada Lovelace",
        email="ada@example.com",
        city="London",
        country="UK",
        location="Remote",
        roles=["Operations Manager", "Buyer"],
        provider="deepseek",
        api_key="sk-test",
        resume_text="Built factories and freight lanes.",
    )

    assert "factories" in (tmp_path / "resume.txt").read_text(encoding="utf-8")
    assert "Ada Lovelace" in (tmp_path / "profile.json").read_text(encoding="utf-8")
    searches = (tmp_path / "searches.yaml").read_text(encoding="utf-8")
    assert "Operations Manager" in searches
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "JOBMATCH_LLM_API_KEY=sk-test" in env
    assert "JOBMATCH_NOTIFIER=console" in env
    assert "api.deepseek.com" in env


def test_apply_simple_setup_rejects_blank_key(tmp_path, monkeypatch):
    from jobmatch import simple_setup

    monkeypatch.setattr(simple_setup, "ensure_dirs", lambda: None)
    try:
        simple_setup.apply_simple_setup(
            full_name="Ada",
            email="",
            city="",
            country="UK",
            location="Remote",
            roles=["Buyer"],
            provider="openai",
            api_key="  ",
            resume_text="hello",
        )
    except ValueError as exc:
        assert "API key" in str(exc)
    else:
        raise AssertionError("expected ValueError")
