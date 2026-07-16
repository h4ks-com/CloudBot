"""redlib.py - Rewrite reddit.com links to a redlib instance

Usage:
  .redlib [nick] - Rewrite your own (or [nick]'s) last posted reddit.com URL
"""

import re
from urllib.parse import ParseResult, urlparse, urlunparse

from cloudbot import hook

REDLIB_INSTANCE: str = "redlib.nadeko.net"

# Reddit host variants we'll rewrite
REDDIT_HOSTS: set[str] = {
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "new.reddit.com",
    "np.reddit.com",
    "mobile.reddit.com",
    "i.reddit.com",
    "compact.reddit.com",
}

# Match URLs in chat
URL_RE: re.Pattern[str] = re.compile(r"https?://\S+")

# (channel, nick_lower) -> last reddit URL
url_cache: dict[tuple[str, str], str] = {}


def _is_reddit_url(url: str) -> bool:
    parsed: ParseResult = urlparse(url)
    return bool(parsed.hostname) and parsed.hostname.lower() in REDDIT_HOSTS


def _rewrite(url: str) -> str | None:
    parsed: ParseResult = urlparse(url)
    if parsed.hostname and parsed.hostname.lower() in REDDIT_HOSTS:
        new: ParseResult = parsed._replace(
            netloc=REDLIB_INSTANCE, scheme="https"
        )
        return urlunparse(new)
    return None


@hook.regex(URL_RE)
def redlib_track(match: re.Match[str], nick: str, chan: str) -> None:
    url: str = match.group(0).rstrip(",.)>!\"'")
    if _is_reddit_url(url):
        url_cache[(chan, nick.lower())] = url


@hook.command("redlib")
def redlib(text: str, nick: str, chan: str, notice) -> str | None:
    """[nick] - Rewrite your own (or [nick]'s) last reddit.com URL to a redlib instance"""
    target: str = text.strip().lower() if text.strip() else nick.lower()
    key: tuple[str, str] = (chan, target)

    if key not in url_cache:
        who: str = target if target != nick.lower() else "you"
        notice(f"No recent reddit link found for {who}.")
        return None

    url: str = url_cache[key]
    rewritten: str | None = _rewrite(url)
    if rewritten:
        return rewritten
    notice("That URL doesn't appear to be a reddit link.")
    return None
