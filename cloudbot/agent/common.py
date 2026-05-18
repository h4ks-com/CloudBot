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
from typing import Any

import openai
import requests
from agents import RunContextWrapper

logger = logging.getLogger("cloudbot")

# Boundary errors caught in tool wrappers. Anything else is a real bug —
# let it propagate so we see it in logs instead of swallowing silently.
# openai.APIError covers RateLimitError, AuthenticationError, BadRequestError,
# APIConnectionError — all the failure modes any tool that calls AsyncOpenAI
# can hit. Without it a 429 in describe_image kills the whole agent run.
TOOL_BOUNDARY_ERRORS: tuple[type[BaseException], ...] = (
    TypeError,
    KeyError,
    AttributeError,
    ValueError,
    requests.RequestException,
    json.JSONDecodeError,
    openai.APIError,
    OSError,
    RuntimeError,
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


def parse_namespace(data: dict[str, Any], ctx: RunContextWrapper) -> str:
    return str(data.get("namespace") or ctx.context.chan or "global").strip()[
        :100
    ]


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
