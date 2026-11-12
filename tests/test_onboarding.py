"""Telegram connect and daily schedule: the two steps a new user cannot do by hand."""

import httpx
import pytest

from jobmatch import schedule, telegram_setup

TOKEN = "123456789:" + "A" * 35


def _client(updates=None, *, status=200, sent=None):
    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if status != 200:
            return httpx.Response(status, json={"ok": False, "description": "Unauthorized"})
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "my_jobs_bot"}})
        if method == "getUpdates":
            return httpx.Response(200, json={"ok": True, "result": updates or []})
        if method == "sendMessage":
            sent.append(request.content)
            return httpx.Response(200, json={"ok": True, "result": {}})
        return httpx.Response(404, json={"ok": False})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_chat_id_is_read_from_updates_not_typed():
    updates = [
        {"message": {"chat": {"id": 111, "first_name": "Old"}}},
        {"message": {"chat": {"id": 222, "first_name": "Ana", "type": "private"}}},
        {"message": {"chat": {"id": 222, "first_name": "Ana", "type": "private"}}},
    ]
    chats = telegram_setup.find_chats(TOKEN, _client(updates))
    assert [c["id"] for c in chats] == [222, 111]  # newest first, one per chat
    assert chats[0]["name"] == "Ana"


def test_bad_token_error_does_not_echo_the_token():
    with pytest.raises(telegram_setup.TelegramSetupError) as err:
        telegram_setup.bot_name(TOKEN, _client(status=401))
    assert TOKEN not in str(err.value)
    with pytest.raises(telegram_setup.TelegramSetupError, match="does not look like"):
        telegram_setup.bot_name("not-a-token")


def test_send_test_hits_the_chosen_chat():
    sent = []
    telegram_setup.send_test(TOKEN, 222, _client(sent=sent))
    assert b'"chat_id":222' in sent[0].replace(b" ", b"")


def test_merge_env_replaces_and_keeps_other_keys():
    before = "# header\nJOBMATCH_LLM_API_KEY=k\nJOBMATCH_NOTIFIER=console\n"
    after = telegram_setup.merge_env(before, telegram_setup.env_lines(TOKEN, 222))
    assert "JOBMATCH_LLM_API_KEY=k" in after and "# header" in after
    assert "JOBMATCH_NOTIFIER=telegram" in after and "console" not in after
    assert after.count("JOBMATCH_TELEGRAM_CHAT_ID=") == 1


def test_cron_line_runs_the_whole_pipeline_once_a_day():
    line = schedule.cron_line("07:30", "lane")
    assert line.startswith("30 7 * * * ")
    assert "--profile lane run --notify" in line
    assert line.endswith("# jobmatch-daily:lane")


def test_installing_twice_replaces_instead_of_duplicating():
    first = schedule.merge_crontab("0 1 * * * other-job\n", schedule.cron_line("07:00"))
    second = schedule.merge_crontab(first, schedule.cron_line("08:15"))
    assert "other-job" in second
    assert second.count("# jobmatch-daily:default") == 1
    assert "15 8 * * *" in second and "0 7 * * *" not in second


def test_profiles_get_separate_entries():
    both = schedule.merge_crontab(schedule.cron_line("07:00", "a") + "\n", schedule.cron_line("07:00", "b"), "b")
    assert both.count("# jobmatch-daily:") == 2


@pytest.mark.parametrize("bad", ["7", "25:00", "07:60", "seven"])
def test_bad_times_are_refused(bad):
    with pytest.raises(ValueError):
        schedule.parse_time(bad)


def test_launchd_plist_has_time_and_command():
    plist = schedule.launchd_plist("06:05")
    assert "<integer>6</integer>" in plist and "<integer>5</integer>" in plist
    assert "<string>run</string>" in plist and "<string>--notify</string>" in plist


def test_custom_runtime_dir_survives_into_cron(monkeypatch):
    monkeypatch.setenv("JOBMATCH_DIR", "/data/my jobs")
    assert "JOBMATCH_DIR='/data/my jobs' " in schedule.cron_line("07:00")
    monkeypatch.delenv("JOBMATCH_DIR")
    assert "JOBMATCH_DIR" not in schedule.cron_line("07:00")
