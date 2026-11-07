"""Rewrites links to alternative frontends and embed fixers.

Usage:
  .alt <url or text>  - rewrite the links in it
  .alt as a reply     - rewrite the links in the message it replies to
  .alt                - rewrite the last message in the channel with such a link
"""

import re
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from cloudbot import hook
from cloudbot.hook import Arg
from cloudbot.util import formatting
from cloudbot.util.messages import ChatMessage

# Each domain covers its subdomains. We pick instances from
# https://github.com/libredirect/instances/blob/main/data.json and check them in a
# browser, since most sit behind a bot check that curl cannot pass.
ALT_HOSTS: dict[str, str] = {
    "reddit.com": "redlib.nadeko.net",
    "twitter.com": "nitter.net",
    "x.com": "nitter.net",
    "youtube.com": "inv.nadeko.net",
    "youtu.be": "inv.nadeko.net",
    "imgur.com": "imgur.artemislena.eu",
    "genius.com": "dumb.bloat.cat",
    "stackoverflow.com": "code.whatever.social",
    # We map these to embed fixers, which give chat previews and send browsers
    # on to the original post.
    "tiktok.com": "www.tnktok.com",
    "facebook.com": "facebed.com",
    "bsky.app": "fxbsky.app",
    "pixiv.net": "www.phixiv.net",
}

CTCP = "\x01"
ACTION = CTCP + "ACTION "
URL_RE = re.compile(r"https?://[^\s<>\"'\x01]+")


def _alt_host(host: str) -> str | None:
    host = host.lower()
    return next(
        (
            alt
            for domain, alt in ALT_HOSTS.items()
            if host == domain or host.endswith("." + domain)
        ),
        None,
    )


def _rewrite_url(match: re.Match[str]) -> str:
    url = match.group(0)
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return url
    alt = _alt_host(host)
    if not alt:
        return url
    return urlunsplit(parts._replace(scheme="https", netloc=alt))


def rewrite(text: str) -> str:
    return URL_RE.sub(_rewrite_url, text)


def _last_rewritable(history, command_prefix: str) -> tuple[str, str] | None:
    for nick, _ts, msg in reversed(history):
        if not msg.startswith(command_prefix) and rewrite(msg) != msg:
            return nick, msg
    return None


def _quote(nick: str, line: str) -> str:
    if line.startswith(ACTION):
        return f"* {nick} {line.removeprefix(ACTION).strip(CTCP)}"
    return f"<{nick}> {line}" if nick else line


@hook.command("alt", "altfrontend", "fixurl", "redlib", autohelp=False)
def alt(
    conn,
    chan: str,
    reply_to: ChatMessage | None,
    text: Annotated[str, Arg("A link, or text with links")] = "",
) -> str:
    """[url] - rewrite links to alternative frontends, or the replied or last message's"""
    if text:
        nick, line = "", text
    elif reply_to:
        nick, line = reply_to.nick, reply_to.text
    else:
        nick, line = _last_rewritable(
            conn.history.get(chan, ()), conn.config["command_prefix"]
        ) or ("", "")
    new = rewrite(line)
    if new == line:
        return "no known link here, reply to a message with one or give a url"
    return formatting.truncate(_quote(nick, new), 420)
