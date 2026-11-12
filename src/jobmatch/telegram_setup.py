"""Connect a Telegram bot without asking the user for a chat id.

Flow: the user creates a bot with @BotFather, pastes its token, and sends any
message to the bot. getUpdates then shows which chat wrote to it, so the chat
id is read rather than typed. A test message proves the pair works before
anything is saved.

Tokens are never logged or echoed back: errors name the step, not the URL.
"""

from __future__ import annotations

import re

import httpx

API = "https://api.telegram.org"
TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")


class TelegramSetupError(ValueError):
    """A message safe to show the user (no token inside)."""


def _call(token: str, method: str, client: httpx.Client | None = None, **params) -> dict:
    if not TOKEN_RE.match(token.strip()):
        raise TelegramSetupError("That does not look like a bot token. Copy the whole line BotFather sent.")
    own = client is None
    client = client or httpx.Client(timeout=20, trust_env=False)
    try:
        resp = client.post(f"{API}/bot{token.strip()}/{method}", json=params)
    except httpx.HTTPError:
        raise TelegramSetupError("Could not reach Telegram. Check the internet connection.") from None
    finally:
        if own:
            client.close()
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code == 401:
        raise TelegramSetupError("Telegram rejected that token. Make a new one with /token in @BotFather.")
    if not data.get("ok"):
        raise TelegramSetupError(f"Telegram said: {data.get('description') or resp.status_code}")
    return data["result"]


def bot_name(token: str, client: httpx.Client | None = None) -> str:
    return "@" + str(_call(token, "getMe", client).get("username", "your_bot"))


def find_chats(token: str, client: httpx.Client | None = None) -> list[dict]:
    """Chats that have messaged the bot, newest first, one entry per chat."""
    seen: dict[int, dict] = {}
    for update in reversed(_call(token, "getUpdates", client, timeout=0)):
        message = update.get("message") or update.get("channel_post") or update.get("my_chat_member") or {}
        chat = message.get("chat") or {}
        if "id" in chat and chat["id"] not in seen:
            name = chat.get("title") or " ".join(
                p for p in (chat.get("first_name"), chat.get("last_name")) if p
            ) or chat.get("username") or str(chat["id"])
            seen[chat["id"]] = {"id": chat["id"], "name": name, "type": chat.get("type", "")}
    return list(seen.values())


def send_test(token: str, chat_id: int | str, client: httpx.Client | None = None) -> None:
    _call(token, "sendMessage", client, chat_id=chat_id,
          text="JobMatch is connected. Job matches will arrive here.")


def env_lines(token: str, chat_id: int | str) -> dict[str, str]:
    return {
        "JOBMATCH_NOTIFIER": "telegram",
        "JOBMATCH_TELEGRAM_BOT_TOKEN": token.strip(),
        "JOBMATCH_TELEGRAM_CHAT_ID": str(chat_id),
    }


def merge_env(existing: str, updates: dict[str, str]) -> str:
    """Replace or append KEY=value lines, keeping everything else as written."""
    out, done = [], set()
    for line in existing.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in updates:
            out.append(f"{key}={updates[key]}")
            done.add(key)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in done]
    return "\n".join(out).rstrip("\n") + "\n"
