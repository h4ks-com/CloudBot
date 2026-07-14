"""Z.AI Web Search MCP transport for the GLM Coding Plan.

Pure client code, no FunctionTool definitions. The web_research tool in
``tools/web.py`` calls :func:`mcp_search`. The coding plan only exposes web
search through the hosted MCP server; the direct ``/paas/v4/web_search``
endpoint requires pay-as-you-go API credits and returns ``1113 Insufficient
balance`` for coding-plan keys, so we use the JSON-RPC-over-SSE transport
(the same shape as ``github_client.py``).

Activation is implicit: if ``api_keys.z_ai`` is set we try Z.AI first; if the
key is missing or the call fails, the caller falls back to SearXNG.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, TypedDict

import requests

from cloudbot.agent.common import run_in_executor

logger = logging.getLogger("cloudbot")

_ENDPOINT_URL = "https://api.z.ai/api/mcp/web_search_prime/mcp"
_SSE_FIELDS = ("data:", "event:", "id:", "retry:")

DEFAULT_CONTENT_SIZE = "medium"
DEFAULT_LOCATION = "us"


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One hit from ``web_search_prime``."""

    title: str
    link: str
    content: str
    media: str = ""
    publish_date: str = ""
    refer: str = ""


class SearchError(Exception):
    """Raised when the Z.AI web search MCP call cannot be completed.

    Callers catch this to fall back to the SearXNG path.
    """


class _RawSearchHit(TypedDict, total=False):
    """Parsed JSON shape of one hit returned by ``web_search_prime``."""

    title: str
    link: str
    content: str
    media: str
    publish_date: str
    refer: str


class _McpContentItem(TypedDict, total=False):
    type: str
    text: str


class _McpResult(TypedDict, total=False):
    content: list[_McpContentItem]


class _JsonRpcError(TypedDict, total=False):
    code: int
    message: str


class _JsonRpcPayload(TypedDict, total=False):
    """JSON-RPC 2.0 response envelope (subset of fields we read)."""

    result: _McpResult
    error: _JsonRpcError


def _parse_sse_payload(text: str) -> _JsonRpcPayload:
    """Return the first JSON-RPC payload found in an SSE response body.

    The MCP server embeds literal newlines inside JSON string values, so one
    ``data:`` event can span many physical lines. Continuation lines are joined
    with the JSON newline escape so ``json.loads`` can parse the result.
    """
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].startswith("data:"):
            index += 1
            continue
        chunk_parts = [lines[index][5:]]
        next_index = index + 1
        while next_index < len(lines):
            line = lines[next_index]
            if not line or any(
                line.startswith(prefix) for prefix in _SSE_FIELDS
            ):
                break
            chunk_parts.append(line)
            next_index += 1
        chunk = "\\n".join(chunk_parts).strip()
        index = next_index
        if not chunk or chunk == "[DONE]":
            continue
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and (
            "result" in parsed or "error" in parsed
        ):
            return parsed  # type: ignore[return-value]
    raise SearchError("no result in SSE stream")


def _extract_search_hits(payload: _JsonRpcPayload) -> list[_RawSearchHit]:
    """Pull the list of raw hit dicts out of an MCP tools/call payload.

    ``web_search_prime`` returns one content item of type ``text`` whose text is
    a JSON-encoded array of hit fields. Raises :class:`SearchError` when the
    payload indicates failure or has an unexpected shape.
    """
    error = payload.get("error")
    if error is not None:
        message = error.get("message", str(error))
        raise SearchError(f"server error: {message}")

    result = payload.get("result")
    if not isinstance(result, dict):
        raise SearchError("malformed result envelope")

    content = result.get("content", [])
    if not isinstance(content, list):
        raise SearchError("unexpected content shape")

    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        try:
            decoded = json.loads(item.get("text", ""))
        except json.JSONDecodeError:
            continue
        # web_search_prime double-encodes the hits array: the text field is a
        # JSON string whose value is itself a JSON-encoded list.
        if isinstance(decoded, str):
            try:
                decoded = json.loads(decoded)
            except json.JSONDecodeError:
                continue
        if isinstance(decoded, list):
            return decoded
    raise SearchError("no hits in response")


def _coerce_hit(raw_hit: Any) -> SearchResult:
    """Best-effort coercion of a parsed hit into a :class:`SearchResult`."""
    if not isinstance(raw_hit, dict):
        raise SearchError(f"non-object hit: {type(raw_hit).__name__}")
    return SearchResult(
        title=str(raw_hit.get("title") or "").strip(),
        link=str(raw_hit.get("link") or "").strip(),
        content=str(raw_hit.get("content") or "").strip(),
        media=str(raw_hit.get("media") or "").strip(),
        publish_date=str(raw_hit.get("publish_date") or "").strip(),
        refer=str(raw_hit.get("refer") or "").strip(),
    )


async def mcp_search(
    bot: Any,
    query: str,
    *,
    max_results: int,
    content_size: str = DEFAULT_CONTENT_SIZE,
    location: str = DEFAULT_LOCATION,
    search_recency_filter: str | None = None,
    search_domain_filter: str | None = None,
) -> list[SearchResult]:
    """Call Z.AI ``web_search_prime`` via MCP.

    Returns at most ``max_results`` hits. Raises :class:`SearchError` when the
    call cannot be completed (no api key, transport failure, server error, or
    empty results) so the caller can fall back to SearXNG.
    """
    api_key = bot.config.get_api_key("z_ai")
    if not api_key:
        raise SearchError("no z_ai api key configured")

    arguments: dict[str, Any] = {
        "search_query": query,
        "content_size": content_size,
        "location": location,
    }
    if search_recency_filter:
        arguments["search_recency_filter"] = search_recency_filter
    if search_domain_filter:
        arguments["search_domain_filter"] = search_domain_filter

    headers = {
        "Authorization": f"Bearer {api_key}",
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
        init_response = await run_in_executor(
            requests.post,
            _ENDPOINT_URL,
            json=init_payload,
            headers=headers,
            timeout=15,
        )
        init_response.raise_for_status()
        session_id = init_response.headers.get("mcp-session-id", "")

        call_headers = {**headers}
        if session_id:
            call_headers["mcp-session-id"] = session_id
        call_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "web_search_prime", "arguments": arguments},
        }
        call_response = await run_in_executor(
            requests.post,
            _ENDPOINT_URL,
            json=call_payload,
            headers=call_headers,
            timeout=30,
        )
        call_response.raise_for_status()
        # The MCP server streams SSE with no charset, so requests defaults to
        # ISO-8859-1 and mangles multibyte UTF-8. Pin UTF-8 so summaries round-trip.
        call_response.encoding = "utf-8"
    except requests.RequestException as request_error:
        logger.warning("zai web search request failed: %s", request_error)
        raise SearchError(f"request failed: {request_error}") from request_error

    payload = _parse_sse_payload(call_response.text)
    raw_hits = _extract_search_hits(payload)
    if not raw_hits:
        raise SearchError("empty result set")

    cap = max(1, min(50, max_results))
    return [_coerce_hit(hit) for hit in raw_hits[:cap]]
