"""GitHub MCP transport, response parsing, fork polling, and per-run budgets.

Pure client code — no FunctionTool definitions. The github tools in
`tools/github.py` and `tools/vibegame.py` import from here.
"""

import asyncio
import logging
import re
from typing import Any

import requests

from cloudbot.agent.common import run_in_executor
from cloudbot.agent.mcp_client import extract_mcp_content, parse_sse

logger = logging.getLogger("cloudbot")

SHA_PATTERN = re.compile(r"\(SHA:\s*([0-9a-f]{6,64})\)")
STALE_SHA_PATTERN = re.compile(r"Current file SHA is ([0-9a-f]{6,64})", re.I)

# MCP methods that only read a public repo, so anyone may use them. Everything
# else commits, forks or opens something under the bot's own GitHub identity and
# stays behind botcontrol. An allowlist, so a method added later is locked until
# someone decides it is safe.
_READONLY_METHODS = frozenset({"get_file_contents", "search_code"})

# Per-run budgets — counters live on the IRC event so they reset per .ask call.
BUDGETS: dict[str, int] = {
    # Reading is the common case and is not a step towards anything — most asks
    # are "what does this repo do". Only high enough to stop a runaway loop.
    "explore": 30,
    "edit": 12,
    "fork": 1,
    "branch": 1,
}


def extract_file_sha(result: dict) -> str | None:
    """Pull blob SHA out of GitHub MCP get_file_contents text content."""
    content = result.get("content", [])
    if not isinstance(content, list):
        return None
    for c in content:
        if not isinstance(c, dict) or c.get("type") != "text":
            continue
        match = SHA_PATTERN.search(c.get("text") or "")
        if match:
            return match.group(1)
    return None


async def mcp_call_raw(event, tool_name: str, args: dict) -> Any:
    """JSON-RPC 2.0 call returning the raw MCP result dict, or an error string.

    Returns dict on success, or a string starting with '(error' / '(mcp error'.
    Callers that need extracted text use mcp_call below.
    """
    bot = event.bot
    if (
        tool_name not in _READONLY_METHODS
        and not event.conn.permissions.has_perm_mask(event.mask, "botcontrol")
    ):
        return (
            f"(error: {tool_name} changes a GitHub repo and needs botcontrol "
            f"permission — {event.nick} is not authorised. Reading is allowed.)"
        )
    cfg = ((bot.config.get("plugins") or {}).get("agent") or {}).get(
        "github_mcp"
    ) or {}
    if not cfg.get("enabled", True):
        return "(error: github_mcp disabled in config)"
    url = cfg.get("url") or ""
    api_key = bot.config.get_api_key(
        cfg.get("api_key_config_path") or "github_mcp_bot"
    )
    github_token = bot.config.get_api_key(
        cfg.get("github_token_config_path") or "github"
    )
    if not url or not api_key or not github_token:
        return (
            "(error: github_mcp url, api key, or github token not configured)"
        )

    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {github_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    init_payload = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "cloudbot-agent", "version": "1.0"},
        },
    }
    try:
        resp = await run_in_executor(
            requests.post,
            url,
            json=init_payload,
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        session_id = resp.headers.get("mcp-session-id", "")

        call_headers = {**headers}
        if session_id:
            call_headers["mcp-session-id"] = session_id
        call_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": args},
        }
        resp2 = await run_in_executor(
            requests.post,
            url,
            json=call_payload,
            headers=call_headers,
            timeout=30,
        )
        resp2.raise_for_status()
        # The MCP server streams SSE with no charset, so requests defaults to
        # ISO-8859-1 and mangles multibyte UTF-8 (box-drawing chars, accents)
        # in .text. Pin UTF-8 so file contents round-trip intact.
        resp2.encoding = "utf-8"
        text = resp2.text

        if "data:" in text:
            result = parse_sse(text)
            return result if result is not None else "(no result in SSE stream)"

        data = resp2.json()
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            return f"(mcp error: {msg})"
        result = data.get("result", {}) if isinstance(data, dict) else {}
        return result if isinstance(result, dict) else {}
    except requests.RequestException as e:
        logger.warning("github_mcp %s request failed: %s", tool_name, e)
        return f"(error calling github mcp: {e})"
    except (ValueError, KeyError, AttributeError, TypeError) as e:
        logger.exception("github_mcp %s parsing failed", tool_name)
        return f"(error parsing github mcp response: {type(e).__name__}: {str(e)[:200]})"


async def mcp_call(event, tool_name: str, args: dict) -> str:
    """JSON-RPC 2.0 call returning extracted text content (or error string)."""
    raw = await mcp_call_raw(event, tool_name, args)
    if isinstance(raw, str):
        return raw
    try:
        return extract_mcp_content(raw)
    except (TypeError, KeyError, AttributeError, ValueError) as e:
        logger.exception("github_mcp %s extract failed", tool_name)
        return f"(error extracting mcp content: {type(e).__name__}: {str(e)[:200]})"


async def wait_for_fork(
    token: str, owner: str, repo: str, max_attempts: int = 10
) -> bool:
    """Poll a fork's metadata until default_branch resolves (or timeout).

    GitHub returns 202 'Fork is in progress' immediately, but the repo may not
    be queryable for a few seconds. Without this poll, the next branch creation
    on the new fork 404s.
    """
    for _ in range(max_attempts):
        try:
            resp = await run_in_executor(
                requests.get,
                f"https://api.github.com/repos/{owner}/{repo}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=5,
            )
            if resp.status_code == 200 and resp.json().get("default_branch"):
                return True
        except requests.RequestException:
            pass
        await asyncio.sleep(3)
    return False


def bump_budget(event, kind: str) -> str | None:
    """Increment a per-event counter; return an error string if over budget.

    Counters live on the event object so they're scoped to one .ask call.
    Budget exhaustion is reported as a tool result so the model sees it
    and changes course (it can't ignore a string it has to read).
    """
    counters = getattr(event, "_agent_budget", None)
    if counters is None:
        counters = {}
        event._agent_budget = counters
    counters[kind] = counters.get(kind, 0) + 1
    cap = BUDGETS.get(kind, 999)
    if counters[kind] <= cap:
        return None
    if kind == "explore":
        return (
            f"(error: read budget exhausted ({counters[kind]}/{cap} read/list/search "
            f"calls). STOP reading and answer with what you already have.)"
        )
    if kind == "edit":
        return (
            f"(error: edit budget exhausted ({counters[kind]}/{cap}). "
            f"STOP editing. Call open_github_pr now with what you have.)"
        )
    if kind == "fork":
        return (
            "(error: already forked once. Reuse the existing fork — "
            "do not call fork_github_repo again.)"
        )
    if kind == "branch":
        return (
            "(error: already created a branch. Reuse it — "
            "do not call create_github_branch again.)"
        )
    return f"(error: {kind} budget exceeded)"
