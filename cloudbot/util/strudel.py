"""Transport for the self-hosted Strudel (rendel) renderer.

rendel speaks the Model Context Protocol over Streamable HTTP at ``{api_url}/mcp``
(behind Kong key-auth). The renderer owns the tools (render/search/sounds/share)
and the composition guide (the ``compose_strudel`` prompt) — this module is just
the JSON-RPC transport plus the client-side strudel.cc share-link helpers (chat
delivery). URL + key come from ``config.json`` ``plugins.strudel``; no defaults.

Used by the ``.strudel`` subagent (``plugins/strudel_agent.py``) and the ``.agi``
tools (``cloudbot/agent/tools/strudel.py``).
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any
from urllib.parse import quote

from cloudbot.util.web import get_session, paste

STRUDEL_REPL = "https://strudel.cc"

TIMEOUT = 120


class StrudelError(Exception):
    """Any failure talking to the rendel MCP server."""


class StrudelNotConfigured(StrudelError):
    """The api_url or api_key is missing from config."""


def config_from_bot(bot: Any) -> tuple[str, str]:
    """Return ``(api_url, api_key)`` from ``plugins.strudel`` config, or raise.

    No defaults: both must be set explicitly in config.json.
    """
    cfg = (bot.config.get("plugins") or {}).get("strudel") or {}
    url = str(cfg.get("api_url") or "").rstrip("/")
    key = str(cfg.get("api_key") or "")
    if not url or not key:
        raise StrudelNotConfigured(
            "Strudel not configured — set plugins.strudel.api_url and api_key in config.json"
        )
    return url, key


# ── MCP transport ────────────────────────────────────────────────────────────
# rendel's MCP server is stateless, so each call is a single JSON-RPC POST — no
# initialize handshake or session id. Responses arrive as SSE
# ("event: message\ndata: {json}") or plain JSON.


def _parse_mcp_body(text: str) -> dict[str, Any]:
    if "data:" in text:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("data:"):
                return json.loads(stripped[len("data:") :].strip())
        return {}
    return json.loads(text)


def mcp_request(
    url: str, key: str, method: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    resp = get_session().post(
        f"{url}/mcp",
        headers={
            "x-api-key": key,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = _parse_mcp_body(resp.text)
    if "error" in data:
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else err
        raise StrudelError(f"MCP {method}: {msg}")
    result = data.get("result")
    return result if isinstance(result, dict) else {}


def list_tools(url: str, key: str) -> list[dict[str, Any]]:
    """The tools rendel's MCP server exposes (name, description, inputSchema)."""
    return mcp_request(url, key, "tools/list").get("tools", [])


def call_tool(
    url: str, key: str, name: str, arguments: dict[str, Any]
) -> tuple[str, bool]:
    """Call an MCP tool; return ``(text, is_error)`` — the concatenated text
    content blocks of the result."""
    res = mcp_request(
        url, key, "tools/call", {"name": name, "arguments": arguments}
    )
    parts = [
        c.get("text", "")
        for c in res.get("content", [])
        if c.get("type") == "text"
    ]
    return "\n".join(p for p in parts if p), bool(res.get("isError"))


def get_prompt_text(url: str, key: str, name: str) -> str:
    """The text of an MCP prompt's first message — used as the agent's
    instructions, so the composition guide lives in rendel, not this bot."""
    res = mcp_request(url, key, "prompts/get", {"name": name, "arguments": {}})
    for msg in res.get("messages", []):
        content = msg.get("content") or {}
        if isinstance(content, dict) and content.get("type") == "text":
            return content.get("text", "")
    return ""


# ── strudel.cc share links (client-side, for chat delivery) ──────────────────


def share_url(code: str) -> str:
    """Build a strudel.cc share link locally — base64 of the UTF-8 source, then
    URL-encoded into the fragment, matching strudel.cc's own code2hash. Pure
    function (no API call); the REPL decodes it with
    base64ToUnicode(decodeURIComponent(hash))."""
    b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
    return f"{STRUDEL_REPL}/#{quote(b64, safe='')}"


def strip_ext(url: str) -> str:
    """Drop the trailing file extension from an s.h4ks URL. The host content-sniffs
    and serves the bare id correctly, so the cleaner extensionless URL works."""
    return re.sub(r"\.[A-Za-z0-9]+$", "", url)


def share_short_url(code: str) -> str:
    """The strudel.cc link wrapped in a short redirect page, so it fits in one
    chat line (the raw link is ~1.5KB and gets truncated). Raises on paste
    failure. The pastebin content-sniffs, so no extension is needed."""
    link = share_url(code)
    html = (
        "<!doctype html><html><head>"
        f'<meta http-equiv="refresh" content="0; url={link}">'
        "<title>Open in Strudel</title></head>"
        f'<body><a href="{link}">Open in Strudel →</a></body></html>'
    )
    return strip_ext(paste(html.encode("utf-8"), raise_on_no_paste=True))
