"""IRCv3 metadata-2: advertise bot ownership.

On the welcome reply (`001`) the bot SETs its own `bot` metadata key
to `<nick> owned by <owner>` so clients with the metadata cap can
render an attribution under the bot's name (e.g. in user popovers
and the bots modal).

Config:
  connection.bot_owner          -- nick (or freeform string) of the human
                                   responsible for this bot. If unset,
                                   the plugin falls back to
                                   cloudbot.bot_owner (global) and then
                                   to "unknown".
  connection.bot_owner_template -- override the rendered metadata value.
                                   `{nick}` and `{owner}` are interpolated.
                                   Default: "{nick} owned by {owner}".
"""

from __future__ import annotations

import logging

from cloudbot import hook

logger = logging.getLogger("cloudbot")


def _resolve_owner(conn) -> str:
    return (
        conn.config.get("bot_owner")
        or conn.bot.config.get("bot_owner")
        or "unknown"
    )


def _render(conn) -> str:
    nick = conn.config.get("nick") or conn.nick or "bot"
    owner = _resolve_owner(conn)
    template = (
        conn.config.get("bot_owner_template")
        or conn.bot.config.get("bot_owner_template")
        or "{nick} owned by {owner}"
    )
    return template.format(nick=nick, owner=owner)


@hook.irc_raw("001")
def set_bot_metadata(conn):
    if conn.memory.get("bot_metadata_set"):
        return
    conn.memory["bot_metadata_set"] = True
    value = _render(conn)
    conn.send(f"METADATA * SET bot :{value}")
    logger.info("bot-metadata: SET bot = %r", value)
