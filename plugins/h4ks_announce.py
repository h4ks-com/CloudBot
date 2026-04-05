"""h4ks portal integration — announcements and #lobby chat forwarding.

Commands:
  .announce <message>  — post an announcement to h4ks.com (botcontrol only)
  .syncchat [n]        — push the last N lobby messages to the portal (botcontrol only)

Events:
  Every message in lobby_channel is forwarded to /api/chat/ automatically.

Config (in config.json under "h4ks"):
  {
    "h4ks": {
      "announce_api_url": "https://h4ks.com/api/announce/",
      "chat_api_url":     "https://h4ks.com/api/chat/",
      "announce_api_token": "your-raw-token-here",
      "lobby_channel": "#lobby"
    }
  }

Token setup on the Django side:
  1. Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
  2. Hash:     python -c "import hashlib; print(hashlib.sha256(b'RAW_TOKEN').hexdigest())"
  3. Create an ApiToken record in Django admin with the hash.
  4. Put the raw token in config.json.
"""

import asyncio
from typing import Any

import aiohttp

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.clients.irc import IrcClient
from cloudbot.event import Event, EventType

_TIMEOUT = aiohttp.ClientTimeout(total=8.0)
_SYNC_DEFAULT = 20


def _cfg(bot: CloudBot) -> dict[str, Any]:
    return bot.config.get("h4ks", {})


@hook.command("announce", permissions=["botcontrol"])
async def announce_cmd(text: str, nick: str, bot: CloudBot) -> str:
    """<message> - post an announcement to the h4ks.com portal"""
    message = text.strip()
    if not message:
        return "usage: .announce <message>"

    cfg = _cfg(bot)
    api_url: str | None = cfg.get("announce_api_url")
    api_token: str | None = cfg.get("announce_api_token")

    if not api_url or not api_token:
        return "announce: not configured (missing h4ks.announce_api_url or announce_api_token)"

    payload = {"body": message, "author": nick, "source": "bot"}
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(api_url, json=payload, headers=headers) as resp:
                if resp.status == 201:
                    data = await resp.json()
                    return f"announced (id:{data.get('id')}) — {message[:60]}"
                body = await resp.text()
                return f"announce failed: HTTP {resp.status} — {body[:80]}"
    except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
        return f"announce error: {e}"


@hook.command("syncchat", permissions=["botcontrol"], autohelp=False)
async def syncchat_cmd(text: str, bot: CloudBot, conn: IrcClient) -> str:
    """[n] - push the last N lobby messages from history to the h4ks.com portal (default 20)"""
    cfg = _cfg(bot)
    api_url: str | None = cfg.get("chat_api_url")
    api_token: str | None = cfg.get("announce_api_token")
    lobby: str = cfg.get("lobby_channel", "#lobby").lower()

    if not api_url or not api_token:
        return "syncchat: not configured (missing h4ks.chat_api_url or announce_api_token)"

    try:
        count = int(text.strip()) if text.strip() else _SYNC_DEFAULT
    except ValueError:
        return "usage: .syncchat [n]"

    try:
        channel_history = conn.history[lobby]
    except KeyError:
        return f"syncchat: no history for {lobby}"

    # history entries are (nick, timestamp, message) — take the last `count`
    messages = list(channel_history)[-count:]

    if not messages:
        return f"syncchat: no messages in history for {lobby}"

    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    sent = 0
    failed = 0

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            for entry_nick, _ts, entry_msg in messages:
                payload = {"nick": entry_nick, "message": entry_msg, "channel": lobby}
                async with session.post(api_url, json=payload, headers=headers) as resp:
                    if resp.status in (200, 201):
                        sent += 1
                    else:
                        failed += 1
    except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
        return f"syncchat error after {sent} sent: {e}"

    return f"syncchat: sent {sent} messages{f', {failed} failed' if failed else ''}"


@hook.event(EventType.message, singlethread=False)
async def forward_chat(event: Event, bot: CloudBot, conn: IrcClient) -> None:
    """Forward #lobby messages to the h4ks.com chat API."""
    cfg = _cfg(bot)
    api_url: str | None = cfg.get("chat_api_url")
    api_token: str | None = cfg.get("announce_api_token")
    lobby: str = cfg.get("lobby_channel", "#lobby").lower()

    if not api_url or not api_token:
        return
    if event.chan.lower() != lobby:
        return
    if event.nick.lower() == conn.nick.lower():
        return

    payload = {"nick": event.nick, "message": event.content, "channel": event.chan}
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(api_url, json=payload, headers=headers) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    print(f"[h4ks_chat] API error {resp.status}: {body[:120]}")
    except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
        print(f"[h4ks_chat] forward failed: {e}")
