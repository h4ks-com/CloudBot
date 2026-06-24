"""IRCv3 draft/metadata-2 support.

Negotiates the capability and, on every connect, sets the bot avatar and a
``bot`` ownership attribution so capable clients can credit the human behind it.
"""

import logging

from cloudbot import hook

logger = logging.getLogger("cloudbot")

_AVATAR_KEY = "avatar"
_AVATAR_PATH = "avatar/botPuppy.png"
_BOT_KEY = "bot"


def _build_avatar_url(bot) -> str:
    gh = (bot.config.get("plugins") or {}).get("agent", {}).get(
        "github_mcp"
    ) or {}
    repo: str = gh.get("self_repo") or "h4ks-com/CloudBot"
    return f"https://raw.githubusercontent.com/{repo}/main/{_AVATAR_PATH}"


def _bot_attribution(conn, bot) -> str:
    nick = conn.config.get("nick") or conn.nick or "bot"
    owner = (
        conn.config.get("bot_owner") or bot.config.get("bot_owner") or "unknown"
    )
    template = (
        conn.config.get("bot_owner_template")
        or bot.config.get("bot_owner_template")
        or "{nick} owned by {owner}"
    )
    return template.format(nick=nick, owner=owner)


@hook.on_cap_available("draft/metadata-2")
def request_metadata() -> bool:
    return True


@hook.on_cap_ack("draft/metadata-2")
def on_metadata_ack(conn) -> None:
    logger.info("[%s] draft/metadata-2 capability enabled", conn.name)


@hook.irc_raw(["376", "422"])
def set_avatar_on_connect(conn, bot) -> None:
    server_caps: dict = conn.memory.get("server_caps", {})
    if not server_caps.get("draft/metadata-2"):
        return
    url = _build_avatar_url(bot)
    conn.cmd("METADATA", "*", "SET", _AVATAR_KEY, url)
    logger.info("[%s|metadata] SET avatar = %s", conn.name, url)
    conn.cmd("METADATA", "*", "SET", _BOT_KEY, _bot_attribution(conn, bot))
    logger.info("[%s|metadata] SET bot attribution", conn.name)
