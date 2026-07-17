"""Shared utilities for agent tool builders.

Lives outside `plugins/` so CloudBot's plugin manager never tries to load this
as a plugin (no @hook decorators here).
"""

import asyncio
import functools
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import openai
import requests
from agents import RunContextWrapper
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger("cloudbot")

# Boundary errors caught in tool wrappers. Anything else is a real bug —
# let it propagate so we see it in logs instead of swallowing silently.
# openai.APIError covers RateLimitError, AuthenticationError, BadRequestError,
# APIConnectionError — all the failure modes any tool that calls AsyncOpenAI
# can hit. Without it a 429 in describe_image kills the whole agent run.
# SQLAlchemyError is here for the same reason: tools touching the shared SQLite
# file hit transient locks ("database is locked") under concurrency, and that is
# a tool-level failure, not a reason to abort the user's whole run.
TOOL_BOUNDARY_ERRORS: tuple[type[BaseException], ...] = (
    TypeError,
    KeyError,
    AttributeError,
    ValueError,
    requests.RequestException,
    json.JSONDecodeError,
    openai.APIError,
    SQLAlchemyError,
    OSError,
    RuntimeError,
)


def recent_chat_snippet(conn: Any, chan: str, n: int = 6) -> str:
    """The last n channel messages, as a reference block for a prompt.

    Every agent here is invoked with one line of text, so without this it cannot
    resolve "that", "again", or who it is answering. The header primes the model
    to read it as background rather than an open task to continue.

    Takes conn+chan rather than an event because the media agents run detached
    from the command that started them and never have one.
    """
    try:
        history = list(conn.history[chan])
    except (KeyError, AttributeError, TypeError):
        return ""
    if not history:
        return ""
    lines = []
    for nick, _ts, msg in history[-n:]:
        msg = msg.replace("\x01ACTION ", "* ").replace("\x01", "")
        lines.append(f"<{nick}> {msg}")
    body = "\n".join(lines)
    return (
        "[recent channel context — reference only, NOT a task to continue]\n"
        f"{body}\n[end recent context]\n"
    )


# Patterns scrubbed from exception messages before they go into pasted reports.
_SECRET_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]+", re.I),
    re.compile(r"\bgithubmcp_[A-Za-z0-9]+", re.I),
    re.compile(r"\bgh[ps]_[A-Za-z0-9]{20,}", re.I),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bapikey[\"':=\s]+[^\s\"',]+", re.I),
]


def parse_args(args_json: str) -> dict[str, Any]:
    """Tolerant JSON arg parser — returns {} on bad input rather than raising."""
    if not args_json:
        return {}
    try:
        parsed = json.loads(args_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# Who a memory is about. Every scope carries its network, so one network's
# memories can never answer another's.
MemoryScope = Literal["network", "channel", "user"]
_ALL_SCOPES: tuple[MemoryScope, ...] = ("user", "channel", "network")
_NAMESPACE_MAX = 100


def memory_namespace(event: Any, scope: MemoryScope) -> str:
    """Where a memory of this scope is written.

    Empty when the event cannot supply what the scope needs — a nick for "user",
    a channel for "channel", a network for any of them — which is the caller's
    signal to refuse rather than invent an unscoped home for it.
    """
    conn = getattr(event, "conn", None)
    network = (getattr(conn, "name", "") or "").strip()
    if not network:
        return ""
    if scope == "network":
        return network[:_NAMESPACE_MAX]
    if scope == "channel":
        chan = (getattr(event, "chan", "") or "").strip()
        return f"{network}/{chan}"[:_NAMESPACE_MAX] if chan else ""
    nick = (getattr(event, "nick", "") or "").strip()
    return f"{network}/{nick}"[:_NAMESPACE_MAX] if nick else ""


def memory_read_namespaces(
    event: Any, scope: MemoryScope | None = None
) -> list[str]:
    """Namespaces a read covers. ``scope`` of None means every scope at once,
    which is what makes a saved fact come back on its own when it is relevant.
    """
    namespaces = [
        memory_namespace(event, wanted)
        for wanted in ((scope,) if scope else _ALL_SCOPES)
    ]
    return [n for n in dict.fromkeys(namespaces) if n]


def parse_scope(data: dict[str, Any]) -> MemoryScope | None:
    """The scope a tool call asked for, or None if it named none."""
    raw = str(data.get("scope") or "").strip().lower()
    if raw == "network":
        return "network"
    if raw == "channel":
        return "channel"
    if raw == "user":
        return "user"
    return None


def split_repo(repo: str) -> tuple[str, str]:
    """'owner/name' → ('owner', 'name'). Trailing slashes tolerated."""
    parts = repo.split("/")
    return parts[0], parts[-1]


def sanitise_err_message(msg: str) -> str:
    """Strip credentials and tokens before pasting an exception message anywhere user-visible."""
    for pat in _SECRET_PATTERNS:
        msg = pat.sub("<redacted>", msg)
    return msg


def resolve_config_path(bot, dotted: str) -> str | None:
    """Resolve dotted config path. Bare names (no dot) hit `api_keys.<name>`."""
    if "." not in dotted:
        key = bot.config.get_api_key(dotted)
        return key if isinstance(key, str) else None
    node: Any = bot.config
    for part in dotted.split("."):
        if hasattr(node, "get"):
            node = node.get(part)
        else:
            return None
        if node is None:
            return None
    return node if isinstance(node, str) else None


# Cache /user lookups so we don't hit the GitHub API on every agent build.
_GH_USERNAME_CACHE: dict[str, str] = {}


def fetch_github_username(bot) -> str | None:
    """PAT owner login. Cached by token because tokens are pinned in config."""
    token = bot.config.get_api_key("github") or ""
    if not token:
        return None
    if token in _GH_USERNAME_CACHE:
        return _GH_USERNAME_CACHE[token]
    try:
        r = requests.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=5,
        )
        r.raise_for_status()
        login = r.json().get("login")
    except (requests.RequestException, ValueError, KeyError):
        logger.warning("agent: github /user lookup failed", exc_info=True)
        return None
    if not isinstance(login, str) or not login:
        return None
    _GH_USERNAME_CACHE[token] = login
    return login


def fetch_self_repo_push(bot) -> bool:
    """Whether the PAT can push to the configured self_repo (decides fork vs direct)."""
    token = bot.config.get_api_key("github") or ""
    cfg = ((bot.config.get("plugins") or {}).get("agent") or {}).get(
        "github_mcp"
    ) or {}
    self_repo = cfg.get("self_repo") if isinstance(cfg, dict) else None
    if not token or not self_repo:
        return False
    try:
        r = requests.get(
            f"https://api.github.com/repos/{self_repo}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=5,
        )
        r.raise_for_status()
        return bool(r.json().get("permissions", {}).get("push"))
    except (requests.RequestException, ValueError, KeyError):
        return False


ToolInvoke = Callable[[RunContextWrapper, str], Awaitable[str]]


def safe_tool(fn: ToolInvoke) -> ToolInvoke:
    """Wrap a tool's on_invoke so boundary errors return string results.

    openai-agents wraps any tool exception as UserError that aborts the
    entire run. For tools that hit external services we want the model to
    see the error as a tool result and recover (or stop).
    """

    @functools.wraps(fn)
    async def wrapped(ctx: RunContextWrapper, args_json: str) -> str:
        try:
            return await fn(ctx, args_json)
        except TOOL_BOUNDARY_ERRORS as e:
            logger.exception(
                "agent tool %s crashed", getattr(fn, "__qualname__", "?")
            )
            return f"(tool error: {type(e).__name__}: {sanitise_err_message(str(e))[:200]})"

    return wrapped


async def run_in_executor(fn, *args, **kwargs) -> Any:
    """Lift a sync callable into an async run via the default executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))
