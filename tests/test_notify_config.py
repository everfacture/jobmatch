import importlib


def test_notify_prefers_canonical_telegram_env(monkeypatch):
    monkeypatch.setenv("JOBMATCH_TELEGRAM_BOT_TOKEN", "canonical-token")
    monkeypatch.setenv("JOBMATCH_TELEGRAM_CHAT_ID", "canonical-chat")
    monkeypatch.setenv("JOBMATCH_TELEGRAM_THREAD_ID", "123")
    monkeypatch.setenv("BOT_TOKEN", "legacy-token")
    monkeypatch.setenv("CHAT_ID", "legacy-chat")
    monkeypatch.setenv("MESSAGE_THREAD_ID", "456")

    import jobmatch.notify as notify
    importlib.reload(notify)

    assert notify._BOT_TOKEN == "canonical-token"
    assert notify._CHAT_ID == "canonical-chat"
    assert notify._MESSAGE_THREAD_ID == "123"


def test_notify_keeps_legacy_telegram_env(monkeypatch):
    monkeypatch.delenv("JOBMATCH_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("JOBMATCH_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("JOBMATCH_TELEGRAM_THREAD_ID", raising=False)
    monkeypatch.setenv("BOT_TOKEN", "legacy-token")
    monkeypatch.setenv("CHAT_ID", "legacy-chat")
    monkeypatch.setenv("MESSAGE_THREAD_ID", "456")

    import jobmatch.notify as notify
    importlib.reload(notify)

    assert notify._BOT_TOKEN == "legacy-token"
    assert notify._CHAT_ID == "legacy-chat"
    assert notify._MESSAGE_THREAD_ID == "456"
