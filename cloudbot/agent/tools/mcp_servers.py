"""Turn every MCP server named in config into agent tools.

What a server offers is read off the wire at startup, so adding a server is a
config edit and a server that grows a tool needs no change here.
"""

import logging
from typing import Any

from agents import FunctionTool, RunContextWrapper

from cloudbot.agent.common import parse_args, run_in_executor, safe_tool
from cloudbot.agent.mcp_client import MCPServer, call_tool, server_manifest

logger = logging.getLogger("cloudbot")

_MANIFESTS: dict[str, tuple[str, list[dict]]] = {}

_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def load_servers(bot) -> list[MCPServer]:
    """Read every enabled server out of config.

    Auth is optional and takes whichever shape the server wants: a key in a
    named header, a bearer token, or nothing at all for a public server.
    """
    cfg = ((bot.config.get("plugins") or {}).get("agent") or {}).get(
        "mcp_servers"
    ) or {}
    servers = []
    for name, entry in cfg.items():
        if not isinstance(entry, dict) or not entry.get("enabled", True):
            continue
        url = entry.get("url") or ""
        if not url:
            logger.warning("mcp server %s has no url, skipping", name)
            continue

        headers = dict(entry.get("headers") or {})
        key_path = entry.get("api_key_config_path") or ""
        if key_path:
            api_key = bot.config.get_api_key(key_path)
            if not api_key:
                logger.warning(
                    "mcp server %s wants api key %s but it is unset, skipping",
                    name,
                    key_path,
                )
                continue
            headers[entry.get("api_key_header") or "apikey"] = api_key
        token_path = entry.get("bearer_token_config_path") or ""
        if token_path:
            token = bot.config.get_api_key(token_path)
            if not token:
                logger.warning(
                    "mcp server %s wants token %s but it is unset, skipping",
                    name,
                    token_path,
                )
                continue
            headers["Authorization"] = f"Bearer {token}"

        servers.append(
            MCPServer(
                name=name,
                url=url,
                headers=headers,
                timeout_s=int(entry.get("timeout_s") or 30),
            )
        )
    return servers


def _tool_name(server: MCPServer, remote_name: str) -> str:
    """Namespace a remote tool so two servers can both offer a 'search'."""
    if remote_name.startswith(server.name):
        return remote_name
    return f"{server.name}_{remote_name}"


def _schema_for(remote: dict) -> dict[str, Any]:
    schema = remote.get("inputSchema")
    if not isinstance(schema, dict):
        return dict(_EMPTY_SCHEMA)
    # The SDK's tool schema rejects the dialect key a JSON Schema generator adds.
    return {k: v for k, v in schema.items() if k != "$schema"} or dict(
        _EMPTY_SCHEMA
    )


def _describe(server: MCPServer, remote: dict, instructions: str) -> str:
    """The tool's own description plus what the server says it is.

    The server's own words tell the model where a name like 'search_midi' gets
    its results and what can be done with them.
    """
    description = str(remote.get("description") or remote.get("title") or "")
    if not instructions:
        return description
    return f"{description}\n\nAbout {server.name}: {instructions}"


def _build_tool(
    server: MCPServer, remote: dict, instructions: str
) -> FunctionTool | None:
    remote_name = str(remote.get("name") or "").strip()
    if not remote_name:
        return None

    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        args = parse_args(args_json)
        result: str = await run_in_executor(
            call_tool, server, remote_name, args
        )
        return result

    return FunctionTool(
        name=_tool_name(server, remote_name),
        description=_describe(server, remote, instructions),
        params_json_schema=_schema_for(remote),
        on_invoke_tool=safe_tool(on_invoke),
        # Remote schemas are written for MCP, which allows optional arguments
        # that the strict function-calling schema rejects.
        strict_json_schema=False,
    )


def discover(bot) -> None:
    """Ask every configured server what it offers.

    This is the only place that talks to a server about its tools. It runs from
    a startup hook, off the event loop, so building the agent's tool list never
    blocks the bot on a slow or unreachable host. A server that is down at
    startup contributes no tools until the next restart.
    """
    for server in load_servers(bot):
        instructions, tools = server_manifest(server)
        _MANIFESTS[server.name] = (instructions, tools)
        logger.info(
            "mcp: %s offers %d tool(s): %s",
            server.name,
            len(tools),
            ", ".join(str(t.get("name")) for t in tools) or "none",
        )


def build_mcp_tools(bot) -> list[FunctionTool]:
    """Agent tools for whatever `discover` found."""
    tools: list[FunctionTool] = []
    for server in load_servers(bot):
        instructions, remotes = _MANIFESTS.get(server.name, ("", []))
        for remote in remotes:
            built = _build_tool(server, remote, instructions)
            if built is not None:
                tools.append(built)
    return tools
