import re

from cloudbot import hook
from cloudbot.event import EventType

OPT_IN = ["#yelling"]
YELL_RE = re.compile("[^a-zA-Z]")
URL_RE = re.compile(
    r"[a-z]+://\S+", re.IGNORECASE
)  # Ignore possible URLs as they are case-sensitive


@hook.event([EventType.message, EventType.action], clients=["irc"])
def yell_check(conn, chan, content, nick):
    """THIS IS A CUSTOM PLUGIN FOR #YELLING TO MAKE SURE PEOPLE FOLLOW THE RULES."""
    if chan.casefold() not in OPT_IN:
        return

    text = URL_RE.sub("", content)
    text = YELL_RE.sub("", text)
    if not text:
        # Ignore empty strings
        return

    caps_count = sum(1 for c in text if c.isupper())
    if (caps_count / len(text)) < 0.75:
        conn.cmd("KICK", chan, nick, "USE MOAR CAPS YOU TROGLODYTE!")
