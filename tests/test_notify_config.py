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


def test_notify_threshold_can_be_lowered_to_seven(monkeypatch):
    monkeypatch.setenv("JOBMATCH_NOTIFY_THRESHOLD", "7")

    import jobmatch.notify as notify
    importlib.reload(notify)

    assert notify._NOTIFY_THRESHOLD == 7


def test_fetch_pending_jobs_skips_previous_day_role_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBMATCH_DIR", str(tmp_path))
    monkeypatch.setenv("JOBMATCH_NOTIFIER", "console")

    import jobmatch.config.paths as paths
    import jobmatch.config as config
    import jobmatch.database as database
    import jobmatch.notify as notify

    importlib.reload(paths)
    importlib.reload(config)
    importlib.reload(database)
    importlib.reload(notify)

    conn = database.init_db()
    conn.execute(
        """
        INSERT INTO jobs (
            url, title, company, location, site, status, fit_score, scored_at,
            full_description, application_url, notified_at
        ) VALUES (?, ?, ?, ?, ?, 'active', 9, ?, ?, ?, ?)
        """,
        (
            "https://old.example/job-1",
            "Administrative Assistant",
            "Example Co",
            "Surabaya",
            "linkedin",
            "2026-06-29T00:00:00+00:00",
            "Office admin role",
            "https://apply.example/old",
            "2026-06-29T01:00:00+00:00",
        ),
    )
    old_job = dict(conn.execute("SELECT * FROM jobs WHERE url = ?", ("https://old.example/job-1",)).fetchone())
    notify._record_notification_history(conn, old_job, "2026-06-29T01:00:00+00:00")
    conn.execute(
        """
        INSERT INTO jobs (
            url, title, company, location, site, status, fit_score, scored_at,
            full_description, application_url
        ) VALUES (?, ?, ?, ?, ?, 'active', 9, ?, ?, ?)
        """,
        (
            "https://new.example/repost-999",
            "Administrative Assistant",
            "Example Co",
            "Surabaya",
            "linkedin",
            "2026-06-30T00:00:00+00:00",
            "Same job reposted",
            "https://apply.example/new-url",
        ),
    )
    conn.commit()

    pending = notify._fetch_pending_jobs(8)
    assert pending.jobs == []
    assert pending.history_suppressed == 1
    row = conn.execute(
        "SELECT notified_at, notify_error FROM jobs WHERE url = ?",
        ("https://new.example/repost-999",),
    ).fetchone()
    assert row["notified_at"] is not None
    assert row["notify_error"] is None


def test_console_notifier_records_history_for_sent_job(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBMATCH_DIR", str(tmp_path))
    monkeypatch.setenv("JOBMATCH_NOTIFIER", "console")

    import jobmatch.config.paths as paths
    import jobmatch.config as config
    import jobmatch.database as database
    import jobmatch.notify as notify

    importlib.reload(paths)
    importlib.reload(config)
    importlib.reload(database)
    importlib.reload(notify)

    conn = database.init_db()
    conn.execute(
        """
        INSERT INTO jobs (
            url, title, company, location, site, status, fit_score, scored_at,
            full_description, application_url
        ) VALUES (?, ?, ?, ?, ?, 'active', 8, ?, ?, ?)
        """,
        (
            "https://example.test/job",
            "Receptionist",
            "Desk Co",
            "Surabaya",
            "linkedin",
            "2026-06-30T00:00:00+00:00",
            "Front desk role",
            "https://apply.example/job",
        ),
    )
    conn.commit()

    report = notify.ConsoleNotifier().send_digest(8)
    assert report.telegram_cards_sent == 1
    assert report.fresh_jobs_delivered == 1
    assert report.history_suppressed == 0
    assert report.closed_marked_notified == 0
    assert report.failed == 0
    assert conn.execute("SELECT COUNT(*) FROM notification_history").fetchone()[0] == 2
