"""Transport for the self-hosted video-creator MCP server (Hyperframes).

video-mcp speaks the Model Context Protocol over Streamable HTTP at
``{api_url}/mcp`` (behind Kong key-auth). The server owns the tools (search /
download / render / status / analyze) and the authoring guides (MCP
resources) — this module is just the JSON-RPC transport. URL + key come from
``config.json`` ``plugins.hyperframes``; no defaults.

Used by the ``.video`` subagent (``plugins/hyperframes.py``) and the ``.agi``
tool (``cloudbot/agent/tools/hyperframes.py``).
"""

from __future__ import annotations

import json
from typing import Any

import requests

from cloudbot.util.web import get_session

# Aligns with the server's Kong upstream timeout: yt-dlp downloads block until
# done, so a short client timeout would clip an in-progress fetch.
TIMEOUT = 1800


class HyperframesError(Exception):
    """Any failure talking to the video-mcp server."""


class HyperframesNotConfigured(HyperframesError):
    """The api_url or api_key is missing from config."""


def config_from_bot(bot: Any) -> tuple[str, str]:
    """Return ``(api_url, api_key)`` from ``plugins.hyperframes`` config, or raise.

    No defaults: both must be set explicitly in config.json.
    """
    cfg = (bot.config.get("plugins") or {}).get("hyperframes") or {}
    url = str(cfg.get("api_url") or "").rstrip("/")
    key = str(cfg.get("api_key") or "")
    if not url or not key:
        raise HyperframesNotConfigured(
            "Hyperframes not configured — set plugins.hyperframes.api_url and api_key in config.json"
        )
    return url, key


# The MCP server is stateless: each call is a one-shot JSON-RPC POST with no
# initialize handshake or session id. Responses arrive as SSE
# ("event: message\ndata: {json}") or plain JSON.


def _parse_mcp_body(text: str) -> dict[str, Any]:
    if "data:" in text:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("data:"):
                parsed = json.loads(stripped[len("data:") :].strip())
                return parsed if isinstance(parsed, dict) else {}
        return {}
    parsed = json.loads(text)
    return parsed if isinstance(parsed, dict) else {}


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
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise HyperframesError(
            f"HTTP {resp.status_code}: {resp.text[:200]}"
        ) from e
    data = _parse_mcp_body(resp.text)
    if "error" in data:
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else err
        raise HyperframesError(f"MCP {method}: {msg}")
    result = data.get("result")
    return result if isinstance(result, dict) else {}


def list_tools(url: str, key: str) -> list[dict[str, Any]]:
    """The tools video-mcp exposes (name, description, inputSchema)."""
    tools = mcp_request(url, key, "tools/list").get("tools", [])
    return tools if isinstance(tools, list) else []


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


def list_resources(url: str, key: str) -> list[dict[str, Any]]:
    """The authoring guides video-mcp exposes as MCP resources."""
    resources = mcp_request(url, key, "resources/list").get("resources", [])
    return resources if isinstance(resources, list) else []


def read_resource(url: str, key: str, uri: str) -> str:
    """Concatenated text of an MCP resource — the authoring guides become the
    sub-agent's instructions, so the video know-how lives in the server, not here.
    """
    res = mcp_request(url, key, "resources/read", {"uri": uri})
    parts = [
        c.get("text", "") for c in res.get("contents", []) if c.get("text")
    ]
    return "\n\n".join(parts)
