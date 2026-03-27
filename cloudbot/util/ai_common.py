import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Literal

from cloudbot.util import formatting, web

RoleType = Literal["user", "assistant"]

APP_HTML_PROMPT_SUFFIX = (
    "\nMake sure to put everything in a single html file so it can be a single code block"
    " meant to be directly used in a browser as it is. Do not explain, just show the code inside a markdown code block."
)


@dataclass
class Message:
    role: RoleType
    content: str
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


def detect_code_blocks(markdown_text: str) -> list[str]:
    """Extract fenced code blocks. Falls back to unclosed blocks, then full text."""
    closed = re.compile(r"```\S*(.*?)```", re.DOTALL).findall(markdown_text)
    if closed:
        return closed
    unclosed = re.compile(r"```(.*)", re.DOTALL).findall(markdown_text)
    return unclosed if unclosed else [markdown_text]


def get_or_create_history(
    cache: dict[tuple[str, str], Deque[Message]],
    chan: str,
    nick: str,
    maxlen: int,
) -> Deque[Message]:
    channick = (chan, nick)
    if channick not in cache:
        cache[channick] = deque(maxlen=maxlen)
    return cache[channick]


def clear_history(
    cache: dict[tuple[str, str], Deque[Message]],
    chan: str,
    nick: str,
) -> str:
    channick = (chan, nick)
    if channick in cache:
        cache.pop(channick)
        return "Conversation cache cleared."
    return "No conversation cache to clear."


def copy_history(
    cache: dict[tuple[str, str], Deque[Message]],
    chan: str,
    nick: str,
    target: str,
    maxlen: int,
) -> str:
    target_channick = (chan, target)
    if target_channick not in cache:
        return f"No conversation history found for {target}."
    cache[(chan, nick)] = deque(cache[target_channick], maxlen=maxlen)
    return f"Copied {target}'s conversation history into yours ({len(cache[(chan, nick)])} messages)."


def upload_history(nick: str, messages: list[Message], header: str) -> str:
    """Format and upload a conversation as a text paste. Returns URL."""
    bar = "-" * 80
    lb = "\n"
    body = f"{lb}{bar}{lb*2}".join(
        f"{nick if m.role == 'user' else 'bot'}: {m.content}" for m in messages
    )
    text = header + "\n" * 4 + body
    return web.paste(text.encode("utf-8"), ext="txt")


def truncate_or_paste(
    response: str,
    nick: str,
    messages: list[Message],
    header: str,
    prefix: str = "",
    max_len: int = 350,
) -> str:
    """Truncate for IRC. If truncated, upload full text and append URL."""
    truncated = formatting.truncate_str(response, max_len)
    result = f"{prefix}{truncated}" if prefix else truncated
    if len(truncated) < len(response):
        paste_url = upload_history(nick, messages, header)
        return f"{result} (full response: {paste_url})"
    return result


def upload_html_app(html_code: str, model_prefix: str = "") -> str:
    """Upload an HTML app and return IRC-ready paste + preview URL string."""
    url = web.paste(html_code.encode("utf-8").strip(), ext="html")
    paste_url = url.removesuffix(".html") + "/p"
    result = f"{paste_url} - Try online: {url}"
    return f"[{model_prefix}] {result}" if model_prefix else result
