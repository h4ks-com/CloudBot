"""
.redlib - Rewrite reddit.com URLs to a redlib instance.

Tracks the last URL posted by each user per channel, and when invoked,
rewrites any reddit.com / www.reddit.com / old.reddit.com / new.reddit.com
host to a redlib instance (configurable, defaults to redlib.nadeko.net).

Usage:
  .redlib           — rewrite your own last posted URL
  .redlib <nick>    — rewrite <nick>'s last posted URL
"""

import re
import logging
from typing import Any
from urllib.parse import urlparse, urlunparse

from cloudbot import hook

logger = logging.getLogger("cloudbot")

# Per-channel, per-nick URL tracking
last_user_url: dict[tuple[str, str], str] = {}

# Match URLs in messages
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Reddit host variants
REDDIT_HOSTS = {
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "new.reddit.com",
    "np.reddit.com",
    "mobile.reddit.com",
    "i.reddit.com",
    "compact.reddit.com",
}


def _get_redlib_host(bot: Any) -> str:
    """Get the configured redlib instance host, or default."""
    config = bot.config.get("plugins", {}).get("redlib", {})
    return config.get("instance", "redlib.nadeko.net")


@hook.regex(URL_RE)
def track_user_url(match, conn=None, nick=None, chan=None, **kwargs):
    """Track the last URL posted by each user in each channel."""
    if nick and chan:
        last_user_url[(chan, nick.lower())] = match.group(0).rstrip(".,;:!?)")


@hook.command("redlib", autohelp=False)
def redlib(text: str, bot: Any, nick: str, chan: str) -> str:
    """[nick] - Rewrite the last reddit.com URL from you (or [nick]) to a redlib instance

    Usage:
      .redlib         — rewrite your own last posted URL
      .redlib <nick>  — rewrite <nick>'s last posted URL
    """
    target_nick = text.strip().lower() if text and text.strip() else nick.lower()

    url = last_user_url.get((chan, target_nick))
    if not url:
        display = nick if target_nick == nick.lower() else target_nick
        return f"No recent URL found for {display}."

    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.lower() in REDDIT_HOSTS:
        redlib_host = _get_redlib_host(bot)
        rewritten = urlunparse(parsed._replace(netloc=redlib_host))
        return rewritten
    else:
        host = parsed.hostname or "?"
        return f"Last URL for {target_nick} is not a reddit link (got {host})."
