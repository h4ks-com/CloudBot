"""Transport for MCP servers reached over Streamable HTTP.

Servers are declared in config under `plugins.agent.mcp_servers` and their tools
are read off the wire, so a server that grows a tool needs no change here.
"""

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass

import requests

logger = logging.getLogger("cloudbot")

_SSE_FIELDS = ("data:", "event:", "id:", "retry:")

_PROTOCOL_VERSION = "2024-11-05"


def extract_mcp_content(result: dict) -> str:
    """Extract meaningful text from an MCP tool result.

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


def _sse_payloads(text: str) -> Iterator[dict]:
    """Every JSON-RPC payload in an SSE body, in order.

    Servers embed literal newlines inside JSON string values, so one SSE
    'data:' event spans many physical lines. We collect continuation lines
    and join with the JSON newline escape so json.loads can parse.
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
        if isinstance(parsed, dict):
            yield parsed


def parse_sse(text: str) -> dict | None:
    """The first JSON-RPC result dict in an SSE response body."""
    for payload in _sse_payloads(text):
        if "result" in payload:
            result = payload["result"]
            return result if isinstance(result, dict) else {}
    return None


def parse_sse_error(text: str) -> str | None:
    """The first JSON-RPC error message in an SSE response body.

    A server reports a bad argument or an unknown tool this way, and the model
    can only correct itself if it is told which.
    """
    for payload in _sse_payloads(text):
        if "error" in payload:
            err = payload["error"]
            if isinstance(err, dict):
                return str(err.get("message") or err)
            return str(err)
    return None


@dataclass(frozen=True)
class MCPServer:
    name: str
    url: str
    headers: dict[str, str]
    timeout_s: int


def _read_result(response: requests.Response) -> dict | str:
    """The JSON-RPC result dict from a response body, or an '(error …)' string."""
    # Servers stream SSE with no charset, so requests defaults to ISO-8859-1
    # and mangles multibyte UTF-8 in .text.
    response.encoding = "utf-8"
    if "text/event-stream" in response.headers.get("content-type", ""):
        text = response.text
        result = parse_sse(text)
        if result is not None:
            return result
        error = parse_sse_error(text)
        return f"(mcp error: {error})" if error else "(no result in SSE stream)"

    data = response.json()
    if isinstance(data, dict) and "error" in data:
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return f"(mcp error: {msg})"
    result = data.get("result", {}) if isinstance(data, dict) else {}
    return result if isinstance(result, dict) else {}


def _handshake(server: MCPServer) -> tuple[dict | str, dict[str, str]]:
    """Open a session and return what the server said about itself.

    Stateful servers hand out a session id here and reject later calls without
    it; stateless ones ignore it, so carrying it always is safe.
    """
    headers = {
        **server.headers,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    response = requests.post(
        server.url,
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "cloudbot-agent", "version": "1.0"},
            },
        },
        headers=headers,
        timeout=10,
    )
    response.raise_for_status()
    session_id = response.headers.get("mcp-session-id", "")
    if session_id:
        headers["mcp-session-id"] = session_id
    return _read_result(response), headers


def rpc(server: MCPServer, method: str, params: dict) -> dict | str:
    """One JSON-RPC call, returning the result dict or an '(error …)' string."""
    try:
        _, headers = _handshake(server)
        response = requests.post(
            server.url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params,
            },
            headers=headers,
            timeout=server.timeout_s,
        )
        response.raise_for_status()
        return _read_result(response)
    except requests.RequestException as e:
        logger.warning("mcp %s %s request failed: %s", server.name, method, e)
        return f"(error calling {server.name}: {e})"
    except ValueError as e:
        logger.exception("mcp %s %s parsing failed", server.name, method)
        return f"(error parsing {server.name} response: {type(e).__name__})"


def server_manifest(server: MCPServer) -> tuple[str, list[dict]]:
    """Ask a server what it is and what it can do.

    Returns its instructions (empty when it offers none) and its tools. A server
    that is down yields no tools, so the agent simply runs without them.
    """
    try:
        init, headers = _handshake(server)
        response = requests.post(
            server.url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
            headers=headers,
            timeout=server.timeout_s,
        )
        response.raise_for_status()
        listed = _read_result(response)
    except requests.RequestException as e:
        logger.warning("mcp %s discovery failed: %s", server.name, e)
        return "", []
    except ValueError:
        logger.exception("mcp %s discovery parsing failed", server.name)
        return "", []

    instructions = ""
    if isinstance(init, dict):
        instructions = str(init.get("instructions") or "").strip()
    if isinstance(listed, str):
        logger.warning("mcp %s tools/list failed: %s", server.name, listed)
        return instructions, []
    tools = listed.get("tools")
    if not isinstance(tools, list):
        return instructions, []
    return instructions, [t for t in tools if isinstance(t, dict)]


def call_tool(server: MCPServer, tool_name: str, args: dict) -> str:
    """Invoke a remote tool and return its text content."""
    raw = rpc(server, "tools/call", {"name": tool_name, "arguments": args})
    if isinstance(raw, str):
        return raw
    return extract_mcp_content(raw)
