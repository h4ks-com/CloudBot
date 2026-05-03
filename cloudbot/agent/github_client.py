"""GitHub MCP transport, response parsing, fork polling, and per-run budgets.

Pure client code — no FunctionTool definitions. The github tools in
`tools/github.py` and `tools/vibegame.py` import from here.
"""

import asyncio
import json
import logging
import re
from typing import Any, Optional

import requests

from cloudbot.agent.common import run_in_executor

logger = logging.getLogger("cloudbot")

_SSE_FIELDS = ("data:", "event:", "id:", "retry:")
SHA_PATTERN = re.compile(r"\(SHA:\s*([0-9a-f]{6,64})\)")
STALE_SHA_PATTERN = re.compile(r"Current file SHA is ([0-9a-f]{6,64})", re.I)

# Per-run budgets — counters live on the IRC event so they reset per .ask call.
BUDGETS: dict[str, int] = {
    "explore": 8,
    "edit": 12,
    "fork": 1,
    "branch": 1,
}


def extract_mcp_content(result: dict) -> str:
    """Extract meaningful text from a GitHub MCP tool result.

    Prefers resource.text; falls back to text items. If isError=true the
    output is wrapped in '(error: …)' so the agent and tracker treat it
    as a failure (otherwise the model retries 'branch already exists' loops).
    """
    is_err = bool(result.get("isError"))
    content = result.get("content", [])
    if not isinstance(content, list):
        body = json.dumps(result)[:8000]
    else:
        resource_parts = [
            c["resource"]["text"]
            for c in content
            if c.get("type") == "resource"
            and isinstance(c.get("resource"), dict)
            and "text" in c["resource"]
        ]
        if resource_parts:
            body = "\n".join(resource_parts)
        else:
            text_parts = [
                c.get("text", "") for c in content if c.get("type") == "text"
            ]
            body = (
                "\n".join(text_parts)
                if text_parts
                else json.dumps(result)[:8000]
            )
    return f"(error: {body})" if is_err else body


def extract_file_sha(result: dict) -> Optional[str]:
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


def parse_sse(text: str) -> Optional[dict]:
    """Parse an SSE response body and return the first JSON-RPC result dict.

    GitHub MCP embeds literal newlines inside JSON string values, so one
    SSE 'data:' event spans many physical lines. We collect continuation
    lines and join with the JSON newline escape so json.loads can parse.
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].startswith("data:"):
            i += 1
            continue
        chunk_parts = [lines[i][5:]]
        j = i + 1
        while j < len(lines):
            ln = lines[j]
            if not ln or any(ln.startswith(f) for f in _SSE_FIELDS):
                break
            chunk_parts.append(ln)
            j += 1
        chunk = "\\n".join(chunk_parts).strip()
        i = j
        if not chunk or chunk == "[DONE]":
            continue
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "result" in parsed:
            result = parsed["result"]
            return result if isinstance(result, dict) else {}
    return None


async def mcp_call_raw(event, tool_name: str, args: dict) -> Any:
    """JSON-RPC 2.0 call returning the raw MCP result dict, or an error string.

    Returns dict on success, or a string starting with '(error' / '(mcp error'.
    Callers that need extracted text use mcp_call below.
    """
    bot = event.bot
    if not event.conn.permissions.has_perm_mask(event.mask, "botcontrol"):
        return f"(error: GitHub MCP tools require botcontrol permission — {event.nick} is not authorised)"
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


def bump_budget(event, kind: str) -> Optional[str]:
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
            f"(error: exploration budget exhausted ({counters[kind]}/{cap} read+list calls). "
            f"STOP reading. You have enough info — fork, branch, edit, open PR now.)"
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
