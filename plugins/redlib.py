"""redlib.py - Rewrite reddit.com links to a redlib instance

Usage:
  .redlib [nick] - Rewrite your own (or [nick]'s) last posted reddit.com URL
"""

import re
from urllib.parse import urlparse, urlunparse

from cloudbot import hook
from cloudbot.util import web

REDLIB_INSTANCE = "redlib.nadeko.net"

# Reddit host variants we'll rewrite
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

# Match URLs in chat
URL_RE = re.compile(r'https?://\S+')

url_cache = {}  # (chan, nick_lower) -> last reddit URL


def _is_reddit_url(url):
    parsed = urlparse(url)
    return parsed.hostname and parsed.hostname.lower() in REDDIT_HOSTS


def _rewrite(url):
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.lower() in REDDIT_HOSTS:
        new = parsed._replace(netloc=REDLIB_INSTANCE, scheme="https")
        return urlunparse(new)
    return None


@hook.regex(URL_RE)
def redlib_track(match, nick, chan):
    url = match.group(0).rstrip(',.)>!"\'')
    if _is_reddit_url(url):
        url_cache[(chan, nick.lower())] = url


@hook.command("redlib")
def redlib(text, nick, chan, notice):
    """[nick] - Rewrite your own (or [nick]'s) last reddit.com URL to a redlib instance"""
    target = text.strip().lower() if text.strip() else nick.lower()
    key = (chan, target)

    if key not in url_cache:
        who = target if target != nick.lower() else "you"
        notice(f"No recent reddit link found for {who}.")
        return

    url = url_cache[key]
    rewritten = _rewrite(url)
    if rewritten:
        return rewritten
    else:
        notice("That URL doesn't appear to be a reddit link.")
