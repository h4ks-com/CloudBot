import json
import logging
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Deque, Literal

from cloudbot.util import web
from cloudbot.util.multiline import split_long_line

logger = logging.getLogger("cloudbot")

RoleType = Literal["user", "assistant"]

APP_HTML_PROMPT_SUFFIX = (
    "\nMake sure to put everything in a single html file so it can be a single code block"
    " meant to be directly used in a browser as it is. Do not explain, just show the code."
)

_HISTORY_TEMPLATE_PATH = Path(__file__).parent / "history_paste.html"


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


def _js_safe_json(obj) -> str:
    # </script> inside a JSON string terminates the <script> block in HTML.
    # Replacing </ with <\/ is valid JS and invisible to the HTML parser.
    return json.dumps(obj).replace("</", "<\\/")


def _safe_content(content: str) -> str:
    # Wrap raw HTML documents in a code fence so marked renders them as escaped
    # <pre><code> blocks rather than injecting them into the page DOM.
    # Any other raw HTML snippets are handled client-side by DOMPurify.
    stripped = content.lstrip()
    if stripped.lower().startswith(("<!doctype", "<html")):
        return f"```html\n{content}\n```"
    return content


def upload_history(nick: str, messages: list[Message], header: str) -> str:
    """Render conversation as a formatted HTML page and upload. Returns URL."""
    msgs_data = [
        {"role": m.role, "content": _safe_content(m.content), "label": nick}
        for m in messages
    ]
    safe_title = (
        header.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    template = _HISTORY_TEMPLATE_PATH.read_text(encoding="utf-8")
    html = (
        template.replace("__TITLE__", safe_title)
        .replace("__TITLE_JSON__", _js_safe_json(header))
        .replace("__MESSAGES_JSON__", _js_safe_json(msgs_data))
    )
    url: str = web.paste(html.encode("utf-8"), ext="html")
    return url


def format_reply_lines(
    text: str,
    *,
    max_lines: int = 10,
    max_line_bytes: int = 420,
    paste: Callable[[], str] | None = None,
) -> list[str]:
    """Format an answer as up to max_lines IRC lines (each within max_line_bytes
    bytes), preserving its line structure rather than collapsing it to one line.
    On overflow, keep the first max_lines lines and append a paste link (via
    paste()) so the full text stays reachable. The result is meant for
    event.reply(*lines), which batches it via draft/multiline when the server
    supports it or sends one PRIVMSG per line otherwise."""
    text = (text or "").strip()
    if not text:
        return []
    pieces: list[str] = []
    for line in text.splitlines():
        line = line.rstrip()
        if line:
            pieces.extend(split_long_line(line, max_line_bytes))
    if not pieces:
        return []
    if len(pieces) <= max_lines:
        return pieces

    kept = pieces[:max_lines]
    if paste is not None:
        try:
            kept.append(f"… full: {paste()}")
        except (OSError, ValueError, RuntimeError):
            logger.exception("reply paste upload failed")
    return kept


def truncate_or_paste(
    response: str,
    nick: str,
    messages: list[Message],
    header: str,
    prefix: str = "",
    max_lines: int = 10,
    max_line_bytes: int = 420,
) -> list[str]:
    """Multi-line IRC reply for a chat completion. Keeps up to max_lines lines;
    on overflow uploads the full conversation and appends a link."""
    lines = format_reply_lines(
        response,
        max_lines=max_lines,
        max_line_bytes=max_line_bytes,
        paste=lambda: upload_history(nick, messages, header),
    )
    if not prefix:
        return lines
    if lines:
        lines[0] = prefix + lines[0]
        return lines
    return [prefix.rstrip()]


def upload_html_app(html_code: str, model_prefix: str = "") -> str:
    """Upload an HTML app and return IRC-ready paste + preview URL string."""
    url = web.paste(html_code.encode("utf-8").strip(), ext="html")
    paste_url = url.removesuffix(".html") + "/p"
    result = f"{paste_url} - Try online: {url}"
    return f"[{model_prefix}] {result}" if model_prefix else result
